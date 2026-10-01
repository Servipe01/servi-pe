"""Google Sheets access: the public profiles sheet the site reads, and a private sheet."""
import asyncio
import unicodedata


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


PRIVATE_HEADERS = ["ID", "Fecha", "Nombre", "Apellido", "DNI", "WhatsApp origen",
                   "WhatsApp contacto", "Foto DNI (archivo en servidor)", "Estado"]


class AppsScriptSheets:
    """Default: talks to the Apps Script web app attached to the public sheet (apps-script/Code.gs)."""

    def __init__(self, url: str, secret: str):
        import httpx
        self.url, self.secret = url, secret
        self.http = httpx.AsyncClient(timeout=40, follow_redirects=True)

    async def _call(self, payload: dict):
        r = await self.http.post(self.url, json={"secret": self.secret, **payload})
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "sheet update failed"))
        return data

    async def append(self, public_values: dict, private_values: dict):
        await self._call({"action": "append", "public": public_values, "private": private_values})

    async def set_status(self, reg_id: int, public_updates: dict, private_updates: dict):
        await self._call({"action": "status", "id": str(reg_id), "updates": public_updates,
                          "private_updates": private_updates})


class Sheets:
    """Alternative: direct access with a Google service account. Column order comes from each header row."""

    def __init__(self, creds_path: str, public_id: str, private_id: str):
        import gspread  # imported lazily so tests run without Google credentials
        gc = gspread.service_account(filename=creds_path)
        self.public = gc.open_by_key(public_id).get_worksheet(0)
        self.private = gc.open_by_key(private_id).get_worksheet(0) if private_id else None
        self._ensure_id_column(self.public)
        if self.private is not None and not self.private.row_values(1):
            self.private.append_row(PRIVATE_HEADERS)

    @staticmethod
    def _ensure_id_column(ws):
        headers = ws.row_values(1)
        if "id" not in [norm(h) for h in headers]:
            col = len(headers) + 1
            if ws.col_count < col:
                ws.add_cols(col - ws.col_count)
            ws.update_cell(1, col, "ID")

    @staticmethod
    def _row_for(ws, values: dict) -> list:
        headers = ws.row_values(1)
        lookup = {norm(k): v for k, v in values.items()}
        return [lookup.get(norm(h), "") for h in headers]

    @staticmethod
    def _find_row(ws, reg_id: int):
        headers = [norm(h) for h in ws.row_values(1)]
        col = headers.index("id") + 1
        for i, v in enumerate(ws.col_values(col), start=1):
            if i > 1 and str(v).strip() == str(reg_id):
                return i, headers
        return None, headers

    def _append(self, public_values: dict, private_values: dict):
        self.public.append_row(self._row_for(self.public, public_values), value_input_option="RAW")
        if self.private is not None:
            self.private.append_row(self._row_for(self.private, private_values), value_input_option="RAW")

    def _set(self, ws, reg_id: int, updates: dict):
        row, headers = self._find_row(ws, reg_id)
        if row is None:
            return False
        for name, value in updates.items():
            if norm(name) in headers:
                ws.update_cell(row, headers.index(norm(name)) + 1, value)
        return True

    async def append(self, public_values: dict, private_values: dict):
        await asyncio.to_thread(self._append, public_values, private_values)

    async def set_status(self, reg_id: int, public_updates: dict, private_updates: dict):
        await asyncio.to_thread(self._set, self.public, reg_id, public_updates)
        if self.private is not None:
            await asyncio.to_thread(self._set, self.private, reg_id, private_updates)
