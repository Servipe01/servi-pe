import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from servibot.app import create_app, parse_messages
from servibot.config import Settings
from servibot.flow import Bot
from servibot.store import Store
from servibot.wa import WAError, WINDOW_CLOSED

ADMIN = "51981571118"
W = "51987654321"


class FakeWA:
    def __init__(self):
        self.sent = []
        self.closed = set()  # numbers whose 24h window Meta reports closed

    async def send(self, to, msg):
        if to in self.closed and msg["type"] != "template":
            raise WAError(WINDOW_CLOSED, "window closed")
        self.sent.append((to, msg))
        return {}

    async def download_media(self, media_id):
        return b"\xff\xd8fakejpeg", "image/jpeg"

    async def upload_media(self, content, mime):
        return "uploaded123"

    def texts(self, to):
        return [m.get("text", "") for t, m in self.sent if t == to]


class FakeSheets:
    def __init__(self, fail=False):
        self.rows, self.private, self.updates, self.fail = [], [], [], fail

    async def append(self, public, private):
        if self.fail:
            raise RuntimeError("google down")
        self.rows.append(public)
        self.private.append(private)

    async def set_status(self, reg_id, updates, private_updates):
        self.updates.append((reg_id, updates, private_updates))


@pytest.fixture
def env(tmp_path):
    s = Settings(admin_number=ADMIN, data_dir=str(tmp_path), public_base_url="https://bot.servi.pe",
                 verify_token="tok", app_secret="secret")
    wa, sh = FakeWA(), FakeSheets()
    bot = Bot(s, Store(":memory:"), wa, sh)
    bot.store.touch_inbound(ADMIN)  # admin wrote to the bot recently, so the 24h window is open
    return bot, wa, sh


n = 0


def m(frm, text="", reply="", image=False):
    global n
    n += 1
    return {"id": f"m{n}", "from": frm, "type": "image" if image else ("interactive" if reply else "text"),
            "text": text, "reply_id": reply, "media_id": "med1" if image else "", "profile_name": "Juana"}


def run(bot, *msgs):
    for x in msgs:
        asyncio.run(bot.handle(x))


def signup(bot, frm=W, contacto_otro=None):
    run(bot, m(frm, "hola"), m(frm, "maría"), m(frm, reply="doc_dni"), m(frm, "12345678"),
        m(frm, image=True), m(frm, "1, 2"), m(frm, reply="dias_ambos"), m(frm, reply="turno_flexible"),
        m(frm, reply="precio_hora"), m(frm, "S/ 25"))
    if contacto_otro:
        run(bot, m(frm, reply="contacto_otro"), m(frm, contacto_otro))
    else:
        run(bot, m(frm, reply="contacto_si"))
    run(bot, m(frm, image=True), m(frm, reply="confirmar_si"))


def test_full_signup_writes_row_in_site_format(env):
    bot, wa, sh = env
    signup(bot)
    row = sh.rows[0]
    assert row["Nombre"] == "María" and row["Apellido"] == ""  # official name is set at approval
    assert row["Categorias"] == "Limpieza, Niñera"
    assert row["Dias disponibles"] == "Semana y finde"
    assert row["Turno"] == "Horario flexible"
    assert row["Precio (S/)"] == "25" and row["Precio Tipo"] == "Por hora"
    assert row["WhatsApp"] == W
    assert row["Activo"] == "No" and row["Tier"] == "Basico"
    assert row["Foto URL"].startswith("https://bot.servi.pe/fotos/")
    assert "DNI" not in row  # DNI never goes to the public sheet
    assert sh.private[0]["DNI"] == "DNI 12345678"
    assert any("Nuevo perfil #1" in t for t in wa.texts(ADMIN))
    assert any(m_.get("caption") == "Documento del perfil #1" for t, m_ in wa.sent if t == ADMIN)


def test_site_filters_match_saved_values(env):
    # the site filters with includes('semana'), includes('finde') and turno == 'horarioflexible'
    bot, wa, sh = env
    signup(bot)
    dias = sh.rows[0]["Dias disponibles"].lower().replace(" ", "")
    assert "semana" in dias and "finde" in dias
    assert sh.rows[0]["Turno"].lower().replace(" ", "") == "horarioflexible"


def test_other_contact_number(env):
    bot, wa, sh = env
    signup(bot, contacto_otro="912 345 678")
    assert sh.rows[0]["WhatsApp"] == "51912345678"


def test_validation_and_stuck_alert(env):
    bot, wa, sh = env
    run(bot, m(W, "hola"), m(W, "maría"), m(W, reply="doc_dni"))
    assert bot.store.get_session(W)["state"] == "dni"
    run(bot, m(W, "123"), m(W, "abc"), m(W, "99"))
    texts = wa.texts(W)
    assert any("8 dígitos" in t for t in texts)
    assert any("persona" in t for t in texts[-2:])
    assert any("atascado" in t for t in wa.texts(ADMIN))


def test_handoff_relay_and_back(env):
    bot, wa, sh = env
    run(bot, m(W, "hola"), m(W, "maría"), m(W, "persona"))
    assert bot.store.get_session(W)["state"] == "humano"
    assert any("pide hablar con una persona" in t for t in wa.texts(ADMIN))
    run(bot, m(ADMIN, "responder 987654321"))
    run(bot, m(ADMIN, "Hola María, ¿en qué te ayudo?"))
    assert "Hola María, ¿en qué te ayudo?" in wa.texts(W)
    run(bot, m(W, "no encuentro mi DNI jaja"))
    assert any("[María] no encuentro mi DNI jaja" in t for t in wa.texts(ADMIN))
    run(bot, m(ADMIN, "fin"))
    assert "*documento de identidad*" in wa.texts(W)[-1]
    run(bot, m(W, reply="doc_dni"), m(W, "12345678"))
    assert bot.store.get_session(W)["state"] == "dni_foto"


def test_approve_publishes_and_notifies(env):
    bot, wa, sh = env
    signup(bot)
    run(bot, m(ADMIN, "pendientes"), m(ADMIN, "aprobar 1"))
    assert sh.updates == []  # needs the DNI name
    assert "aprobar 1 Rosa Huamán Quispe" in wa.texts(ADMIN)[-1]
    run(bot, m(ADMIN, "aprobar 1 MARIA ELENA QUISPE MAMANI"))
    assert sh.updates[-1] == (1, {"Nombre": "Maria Elena", "Apellido": "Quispe Mamani", "Activo": "Sí",
                                  "Verificado": "Sí"},
                              {"Nombre": "Maria Elena", "Apellido": "Quispe Mamani", "Estado": "Aprobado"})
    assert "Maria Elena Q." in wa.texts(ADMIN)[-1]
    assert sum("ya está publicado" in t for t in wa.texts(W)) == 1
    # fixing the split re-approves without messaging the worker again
    run(bot, m(ADMIN, "aprobar 1 María Elena Quispe / Mamani"))
    assert sh.updates[-1][1]["Apellido"] == "Mamani"
    assert sum("ya está publicado" in t for t in wa.texts(W)) == 1
    run(bot, m(W, "hola?"))
    assert "ya está publicado" in wa.texts(W)[-1]


def test_reject_with_reason(env):
    bot, wa, sh = env
    signup(bot)
    run(bot, m(ADMIN, "rechazar 1 la foto del DNI no se lee"))
    assert any("la foto del DNI no se lee" in t for t in wa.texts(W))


def test_window_closed_holds_message_and_sends_template(env):
    bot, wa, sh = env
    wa.closed.add(ADMIN)
    signup(bot)
    assert any(m_["type"] == "template" for t, m_ in wa.sent if t == ADMIN)
    assert bot.store.has_queued(ADMIN)
    wa.closed.discard(ADMIN)
    run(bot, m(ADMIN, "hola"))  # admin writes: held alerts are delivered
    assert any("Nuevo perfil #1" in t for t in wa.texts(ADMIN))
    assert not bot.store.has_queued(ADMIN)


def test_sheet_failure_keeps_registration(env):
    bot, wa, _ = env
    bot.sheets = FakeSheets(fail=True)
    signup(bot)
    assert bot.store.get_registration(1) is not None
    assert any("no se pudo guardar en la hoja" in t for t in wa.texts(ADMIN))


def test_duplicate_webhook_is_ignored(env):
    bot, wa, sh = env
    x = m(W, "hola")
    run(bot, x, x)
    assert len(wa.texts(W)) == 1


def test_parse_interactive_and_image():
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": W, "profile": {"name": "Juana"}}],
        "messages": [
            {"id": "a", "from": W, "type": "interactive",
             "interactive": {"type": "button_reply", "button_reply": {"id": "dias_finde", "title": "Finde"}}},
            {"id": "b", "from": W, "type": "image", "image": {"id": "IMG1"}},
        ]}}]}]}
    a, b = parse_messages(payload)
    assert a["reply_id"] == "dias_finde" and a["profile_name"] == "Juana"
    assert b["media_id"] == "IMG1"


def test_webhook_signature_verify_and_photos(env, tmp_path):
    bot, wa, sh = env
    client = TestClient(create_app(bot.s, bot))
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "tok",
                                          "hub.challenge": "42"}).text == "42"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "bad"}).status_code == 403
    body = json.dumps({"entry": []}).encode()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": sig,
                                                          "content-type": "application/json"}).status_code == 200
    (tmp_path / "fotos" / "abc.jpg").write_bytes(b"x")
    assert client.get("/fotos/abc.jpg").status_code == 200
    (tmp_path / "privado" / "dni.jpg").write_bytes(b"x")
    assert client.get("/fotos/..%2Fprivado%2Fdni.jpg").status_code == 404


def test_apps_script_client_sends_secret_and_raises_on_error():
    import httpx
    from servibot.sheets import AppsScriptSheets
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"ok": body["secret"] == "s3"})

    c = AppsScriptSheets("https://script.example/exec", "s3")
    c.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(c.append({"Nombre": "A"}, {"DNI": "1"}))
    assert seen[0]["action"] == "append" and seen[0]["public"]["Nombre"] == "A"
    c.secret = "wrong"
    with pytest.raises(RuntimeError):
        asyncio.run(c.set_status(1, {"Activo": "Sí"}, {"Estado": "Aprobado"}))


def test_name_splitting_rules():
    from servibot.flow import split_full_name as s
    assert s("rosa") == ("Rosa", "")
    assert s("Rosa Huamán") == ("Rosa", "Huamán")
    assert s("ana lucía pérez rojas") == ("Ana Lucía", "Pérez Rojas")
    assert s("María de los Ángeles Pérez Rojas") == ("María de los Ángeles", "Pérez Rojas")
    assert s("juan carlos de la cruz pérez") == ("Juan Carlos", "de la Cruz Pérez")


def test_app_starts_without_any_configuration(tmp_path, monkeypatch):
    for k in ["SHEETS_URL", "WA_TOKEN", "WA_APP_SECRET"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CREDS", str(tmp_path / "missing.json"))
    client = TestClient(create_app())
    assert client.get("/health").json() == {"ok": True}


def test_privacy_page(env):
    bot, wa, sh = env
    r = TestClient(create_app(bot.s, bot)).get("/privacidad")
    assert r.status_code == 200 and "Política de privacidad" in r.text


U = "PE.1596213328650795"  # a WhatsApp username user: Meta sends an ID instead of the phone number


def test_parse_username_user():
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Victor", "username": "VCuadros"}, "user_id": U}],
        "messages": [{"from_user_id": U, "id": "x", "type": "text", "text": {"body": "Hola"}}]}}]}]}
    (msg,) = parse_messages(payload)
    assert msg["from"] == U and msg["profile_name"] == "Victor" and msg["text"] == "Hola"


def test_send_uses_recipient_for_username_ids():
    from servibot.wa import dest
    assert dest(U) == {"recipient": U}
    assert dest("51987654321") == {"to": "51987654321"}


def test_admin_recognized_by_username_id(env):
    bot, wa, sh = env
    bot.s.admin_user_ids = U
    run(bot, m(U, "hola"))
    assert any("Comandos del equipo" in t for t in wa.texts(ADMIN))


def test_username_worker_is_asked_for_contact_number(env):
    bot, wa, sh = env
    run(bot, m(U, "hola"), m(U, "rosa"), m(U, reply="doc_dni"), m(U, "12345678"), m(U, image=True), m(U, "1"),
        m(U, reply="dias_semana"), m(U, reply="turno_manana"), m(U, reply="precio_hora"), m(U, "20"))
    assert "¿A qué número de WhatsApp te pueden escribir" in wa.texts(U)[-1]
    run(bot, m(U, "987 111 222"), m(U, reply="foto_omitir"), m(U, reply="confirmar_si"))
    assert sh.rows[0]["WhatsApp"] == "51987111222"
    assert any(f"responder {U}" in t or "responder 1" in t for t in wa.texts(ADMIN))


def test_relay_to_username_worker(env):
    bot, wa, sh = env
    run(bot, m(U, "hola"), m(U, "persona"))
    assert any(f"responder {U}" in t for t in wa.texts(ADMIN))
    run(bot, m(ADMIN, f"responder {U.lower()}"), m(ADMIN, "Hola, te ayudo"))
    assert "Hola, te ayudo" in wa.texts(U)


def test_foreign_documents(env):
    bot, wa, sh = env
    run(bot, m(W, "hola"), m(W, "yorgelis"), m(W, reply="doc_ce"))
    assert "Carné de extranjería" in wa.texts(W)[-1]
    run(bot, m(W, "123"))
    assert bot.store.get_session(W)["state"] == "dni"
    run(bot, m(W, "001-234-567"))
    s = bot.store.get_session(W)
    assert s["state"] == "dni_foto" and s["data"]["dni"] == "001234567" and s["data"]["doc_tipo"] == "Carné de extranjería"
    W2 = "51911111111"
    run(bot, m(W2, "hola"), m(W2, "josé"), m(W2, "tengo cpp"), m(W2, "A1234567"))
    assert bot.store.get_session(W2)["data"]["doc_tipo"] == "CPP o PTP"
    assert bot.store.get_session(W2)["state"] == "dni_foto"


def test_destacado_request_goes_to_a_person(env):
    bot, wa, sh = env
    run(bot, m(W, "Hola, quiero mejorar mi perfil a Destacado en Servi.pe"))
    assert any("Destacado" in t for t in wa.texts(ADMIN))
    assert bot.store.get_session(W)["state"] == "humano"
