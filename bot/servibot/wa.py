"""Minimal WhatsApp Cloud API client."""
import httpx

# Meta error code when the 24 hour customer service window is closed
WINDOW_CLOSED = 131047


class WAError(Exception):
    def __init__(self, code: int | None, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class WhatsApp:
    def __init__(self, token: str, phone_id: str, version: str = "v23.0"):
        self.token = token
        self.phone_id = phone_id
        self.base = f"https://graph.facebook.com/{version}"
        self.http = httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}"})

    async def _send(self, payload: dict) -> dict:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", **payload}
        r = await self.http.post(f"{self.base}/{self.phone_id}/messages", json=body)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise WAError(err.get("code"), err.get("message", r.text))
        return data

    async def send(self, to: str, msg: dict) -> dict:
        """Send a message described as {"type": "text"|"buttons"|"list"|"image"|"template", ...}."""
        kind = msg["type"]
        if kind == "text":
            return await self._send({"to": to, "type": "text", "text": {"body": msg["text"], "preview_url": False}})
        if kind == "buttons":
            return await self._send({
                "to": to, "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": msg["text"]},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": bid, "title": title[:20]}} for bid, title in msg["buttons"]
                    ]},
                },
            })
        if kind == "list":
            return await self._send({
                "to": to, "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": msg["text"]},
                    "action": {
                        "button": msg.get("button", "Ver opciones")[:20],
                        "sections": [{"title": msg.get("section", "Opciones")[:24], "rows": [
                            {"id": rid, "title": title[:24]} for rid, title in msg["rows"]
                        ]}],
                    },
                },
            })
        if kind == "image":
            image = {"id": msg["media_id"]} if msg.get("media_id") else {"link": msg["link"]}
            if msg.get("caption"):
                image["caption"] = msg["caption"]
            return await self._send({"to": to, "type": "image", "image": image})
        if kind == "template":
            params = [{"type": "text", "text": p} for p in msg.get("params", [])]
            tpl = {"name": msg["name"], "language": {"code": msg.get("lang", "es")}}
            if params:
                tpl["components"] = [{"type": "body", "parameters": params}]
            return await self._send({"to": to, "type": "template", "template": tpl})
        raise ValueError(f"unknown message type {kind}")

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = (await self.http.get(f"{self.base}/{media_id}")).json()
        r = await self.http.get(meta["url"])
        r.raise_for_status()
        return r.content, meta.get("mime_type", "image/jpeg")

    async def upload_media(self, content: bytes, mime: str) -> str:
        r = await self.http.post(
            f"{self.base}/{self.phone_id}/media",
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": ("file", content, mime)},
        )
        r.raise_for_status()
        return r.json()["id"]
