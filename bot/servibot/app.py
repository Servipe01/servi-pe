"""Web server: Meta webhook endpoint, profile photo hosting, health check."""
import asyncio
import hashlib
import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from .config import Settings
from .flow import Bot
from .store import Store

log = logging.getLogger("servibot")


class UnconfiguredSheets:
    """Used until SHEETS_URL is set: the bot still runs and keeps registrations in its own database."""

    async def append(self, public_values, private_values):
        raise RuntimeError("hoja de Google no conectada todavía (falta SHEETS_URL)")

    async def set_status(self, reg_id, public_updates, private_updates):
        raise RuntimeError("hoja de Google no conectada todavía (falta SHEETS_URL)")


def parse_messages(payload: dict):
    """Turn a Meta webhook payload into simple message dicts."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                kind = m.get("type")
                msg = {"id": m.get("id"), "from": m.get("from"), "type": kind, "text": "",
                       "reply_id": "", "media_id": "", "profile_name": names.get(m.get("from"), "")}
                if kind == "text":
                    msg["text"] = m.get("text", {}).get("body", "")
                elif kind == "interactive":
                    inter = m.get("interactive", {})
                    picked = inter.get("button_reply") or inter.get("list_reply") or {}
                    msg["reply_id"] = picked.get("id", "")
                    msg["text"] = picked.get("title", "")
                elif kind == "button":  # quick reply on a template
                    msg["text"] = m.get("button", {}).get("text", "")
                elif kind == "image":
                    msg["media_id"] = m.get("image", {}).get("id", "")
                    msg["text"] = m.get("image", {}).get("caption", "")
                elif kind == "document" and m.get("document", {}).get("mime_type", "").startswith("image/"):
                    msg["type"] = "image"
                    msg["media_id"] = m["document"]["id"]
                out.append(msg)
    return out


def create_app(settings: Settings | None = None, bot: Bot | None = None) -> FastAPI:
    settings = settings or Settings()
    if bot is None:
        from .sheets import AppsScriptSheets, Sheets
        from .wa import WhatsApp
        store = Store(str(Path(settings.data_dir) / "servibot.db"))
        wa = WhatsApp(settings.wa_token, settings.wa_phone_id, settings.graph_version)
        if settings.sheets_url:
            sheets = AppsScriptSheets(settings.sheets_url, settings.sheets_secret)
        elif Path(settings.google_creds).is_file():
            sheets = Sheets(settings.google_creds, settings.sheet_id, settings.private_sheet_id)
        else:
            sheets = UnconfiguredSheets()
        bot = Bot(settings, store, wa, sheets)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = asyncio.Lock()

    async def process(messages):
        async with lock:  # one message at a time keeps each conversation consistent
            for m in messages:
                try:
                    await bot.handle(m)
                except Exception:
                    log.exception("error handling message %s", m.get("id"))

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/webhook")
    async def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and settings.verify_token and q.get("hub.verify_token") == settings.verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403)

    @app.post("/webhook")
    async def receive(request: Request):
        body = await request.body()
        if settings.app_secret:
            expected = "sha256=" + hmac.new(settings.app_secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
                raise HTTPException(401)
        messages = parse_messages(await request.json())
        if messages:
            asyncio.create_task(process(messages))
        return {"ok": True}

    @app.get("/fotos/{name}")
    async def foto(name: str):
        # only plain generated file names; the private DNI folder is never served
        if not name.endswith(".jpg") or "/" in name or ".." in name or len(name) > 40:
            raise HTTPException(404)
        path = Path(settings.data_dir) / "fotos" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    return app


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
