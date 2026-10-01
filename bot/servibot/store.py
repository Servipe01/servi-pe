"""Small SQLite store: conversation sessions, registrations, relay state, outbox."""
import json
import sqlite3
import time
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    wa_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    retries INTEGER NOT NULL DEFAULT 0,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id TEXT NOT NULL,
    data TEXT NOT NULL,
    status TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS processed (msg_id TEXT PRIMARY KEY, at REAL);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_number TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS last_inbound (wa_id TEXT PRIMARY KEY, at REAL);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # sessions
    def get_session(self, wa_id: str):
        row = self.db.execute("SELECT * FROM sessions WHERE wa_id=?", (wa_id,)).fetchone()
        if not row:
            return None
        return {"state": row["state"], "data": json.loads(row["data"]), "retries": row["retries"]}

    def save_session(self, wa_id: str, state: str, data: dict, retries: int = 0):
        self.db.execute(
            "INSERT INTO sessions (wa_id,state,data,retries,updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(wa_id) DO UPDATE SET state=excluded.state, data=excluded.data, "
            "retries=excluded.retries, updated=excluded.updated",
            (wa_id, state, json.dumps(data, ensure_ascii=False), retries, time.time()),
        )
        self.db.commit()

    # registrations
    def add_registration(self, wa_id: str, data: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO registrations (wa_id,data,status,created) VALUES (?,?,?,?)",
            (wa_id, json.dumps(data, ensure_ascii=False), "pendiente", time.time()),
        )
        self.db.commit()
        return cur.lastrowid

    def get_registration(self, reg_id: int):
        row = self.db.execute("SELECT * FROM registrations WHERE id=?", (reg_id,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "wa_id": row["wa_id"], "data": json.loads(row["data"]), "status": row["status"]}

    def latest_registration_for(self, wa_id: str):
        row = self.db.execute(
            "SELECT id FROM registrations WHERE wa_id=? ORDER BY id DESC LIMIT 1", (wa_id,)
        ).fetchone()
        return self.get_registration(row["id"]) if row else None

    def update_registration_data(self, reg_id: int, data: dict):
        self.db.execute("UPDATE registrations SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), reg_id))
        self.db.commit()

    def set_registration_status(self, reg_id: int, status: str):
        self.db.execute("UPDATE registrations SET status=? WHERE id=?", (status, reg_id))
        self.db.commit()

    def pending_registrations(self):
        rows = self.db.execute("SELECT id FROM registrations WHERE status='pendiente' ORDER BY id").fetchall()
        return [self.get_registration(r["id"]) for r in rows]

    # key/value (relay target etc.)
    def get(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def set(self, k: str, v):
        if v is None:
            self.db.execute("DELETE FROM kv WHERE k=?", (k,))
        else:
            self.db.execute(
                "INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v))
            )
        self.db.commit()

    # idempotency: Meta can deliver the same webhook more than once
    def seen(self, msg_id: str) -> bool:
        try:
            self.db.execute("INSERT INTO processed (msg_id, at) VALUES (?,?)", (msg_id, time.time()))
            self.db.commit()
            return False
        except sqlite3.IntegrityError:
            return True

    # 24 hour window tracking
    def touch_inbound(self, wa_id: str):
        self.db.execute(
            "INSERT INTO last_inbound (wa_id,at) VALUES (?,?) ON CONFLICT(wa_id) DO UPDATE SET at=excluded.at",
            (wa_id, time.time()),
        )
        self.db.commit()

    def window_open(self, wa_id: str, now: float | None = None) -> bool:
        row = self.db.execute("SELECT at FROM last_inbound WHERE wa_id=?", (wa_id,)).fetchone()
        now = time.time() if now is None else now
        return bool(row) and now - row["at"] < 23.5 * 3600

    # outbox: messages held until the recipient reopens the 24 hour window
    def queue(self, to_number: str, payload: dict):
        self.db.execute(
            "INSERT INTO outbox (to_number,payload) VALUES (?,?)", (to_number, json.dumps(payload, ensure_ascii=False))
        )
        self.db.commit()

    def has_queued(self, to_number: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM outbox WHERE to_number=? LIMIT 1", (to_number,)).fetchone())

    def pop_queued(self, to_number: str):
        rows = self.db.execute("SELECT id,payload FROM outbox WHERE to_number=? ORDER BY id", (to_number,)).fetchall()
        self.db.execute("DELETE FROM outbox WHERE to_number=?", (to_number,))
        self.db.commit()
        return [json.loads(r["payload"]) for r in rows]
