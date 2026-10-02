"""Settings, read from environment variables (see deploy/servibot.env.example)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # WhatsApp Cloud API
    wa_token: str = field(default_factory=lambda: _env("WA_TOKEN"))
    wa_phone_id: str = field(default_factory=lambda: _env("WA_PHONE_ID"))
    verify_token: str = field(default_factory=lambda: _env("WA_VERIFY_TOKEN"))
    app_secret: str = field(default_factory=lambda: _env("WA_APP_SECRET"))
    graph_version: str = field(default_factory=lambda: _env("WA_GRAPH_VERSION", "v23.0"))

    # Human handoff: alerts go to this number (international format, no +)
    admin_number: str = field(default_factory=lambda: _env("ADMIN_NUMBER", "51981571118"))
    # Admin's WhatsApp username ID(s) (BSUID like PE.123...), comma separated. Messages from these count as admin.
    admin_user_ids: str = field(default_factory=lambda: _env("ADMIN_USER_ID"))
    # Approved utility templates used when the 24 hour window is closed
    admin_template: str = field(default_factory=lambda: _env("ADMIN_TEMPLATE", "aviso_equipo"))
    worker_template: str = field(default_factory=lambda: _env("WORKER_TEMPLATE", "seguimiento_perfil"))
    template_lang: str = field(default_factory=lambda: _env("TEMPLATE_LANG", "es"))

    # Google Sheets via the Apps Script web app (default)
    sheets_url: str = field(default_factory=lambda: _env("SHEETS_URL"))
    sheets_secret: str = field(default_factory=lambda: _env("SHEETS_SECRET"))
    # or direct access with a service account (used only if SHEETS_URL is empty)
    sheet_id: str = field(default_factory=lambda: _env("SHEET_ID", "1U63yLHUW88m0SeN1QmTNI7HjTP8NTAz22hdAb-R-HdQ"))
    private_sheet_id: str = field(default_factory=lambda: _env("PRIVATE_SHEET_ID"))
    google_creds: str = field(default_factory=lambda: _env("GOOGLE_CREDS", "/etc/servibot/google.json"))

    # Storage and public URLs
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "/var/lib/servibot"))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "https://bot.servi.pe"))
