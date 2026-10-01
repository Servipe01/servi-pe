# Servi.pe project handbook

Read this first in every work session. Update the "Log" and "Status" sections at the end of every session.

## What Servi.pe is

A marketplace in Lima, Peru where blue collar workers offer their services to people.
Categories: Limpieza, Niñera, Gasfitería, Electricidad, Pintura, Carpintería, Jardinería.
Workers are DNI verified. Signup and client contact both happen over WhatsApp.
Pricing: free for the first 150 workers; "Destacado" tier at S/ 29 per month for more visibility.
Tagline: "Encuentra. Contrata. Confía."
Owner: Victor Cuadros.

## How it works today (as of 2026-10-01)

| Piece | Where it lives | Notes |
|---|---|---|
| Website code | GitHub `Servipe01/servi-pe` (public), single file `index.html` | Edited so far through the GitHub web editor |
| Hosting | Vercel, deploys automatically from the GitHub repo | `servi.pe` and `www.servi.pe` DNS point to Vercel |
| Worker data | Google Sheet ID `1U63yLHUW88m0SeN1QmTNI7HjTP8NTAz22hdAb-R-HdQ`, first tab (gid=0) | Site reads it live in the browser via the gviz JSONP endpoint, so the sheet must stay shared as "anyone with the link can view" |
| Worker photos | Google Drive links in the sheet column "Foto URL" | Converted to `drive.google.com/uc?export=view&id=...` by `toImgUrl()` |
| Registration | Manual: workers tap a wa.me link to WhatsApp +51 981 571 118 | Profiles are then typed into the sheet by hand |
| Bot server | DigitalOcean Droplet `servi-pe-server`, IP 142.93.192.86, Ubuntu 24.04, NYC1, USD 6 per month | Serves the bot at `https://142-93-192-86.sslip.io` (free name, no DNS needed; switch to bot.servi.pe later with `BOT_HOST=bot.servi.pe` when the servi.pe DNS is reachable) |
| Bot code | `bot/` folder in this repo (excluded from Vercel by `.vercelignore`) | Python, FastAPI. See "WhatsApp bot" below |

### Sheet columns the site reads

Matching ignores accents and capital letters.

`Nombre`, `Apellido`, `WhatsApp`, `Categorias`, `Tier` (Destacado or Basico), `Verificado`, `Foto URL`, `Precio (S/)`, `Precio Tipo` (default "Por hora"), `Turno`, `Dias disponibles`, `Rating`, `Reviews`, `Activo` (only rows with true, sí or si are shown).

Any new tool that creates profiles (the WhatsApp bot) must write rows in exactly this format.

### Config at the top of the script in index.html

`SHEET_ID`, `WA_NUMBER` (51981571118), `SHOW_COUNTER_AT` (100: the worker counter pill appears publicly at 100 workers).

## Meta / WhatsApp setup

* Business portfolio "Servi.pe", ID 1662630675379820, admin Victor (victorcuadros@gmail.com). Unverified.
* Facebook page ID 629793710223897 (formerly "Twisso", about 3.6K followers) moved into the Servi.pe portfolio; rename to "Servi.pe" requested 2026-10-01 (Meta review up to 3 days; then locked 60 days).
* Victor's other portfolio "Arbiella Group" (verified UAE company, twisso.ai) is deliberately NOT used for Servi.pe.
* Meta app "Servi.pe Bot", app ID 2199185970645186, use case "Connect with customers through WhatsApp".
* WhatsApp Business account ID 4213388552284876. Display name "Servi Peru" (names that look like a URL, such as "Servi.pe", get rejected).
* Bot number +51 907 434 222, phone number ID 1426759753848412. Bot only: never install WhatsApp on this number.
* Pricing (Peru, from 2026-10-01): first 1,000 service messages per month free, then about US$0.01 to 0.03 each; templates about US$0.01 to 0.07.

## WhatsApp bot

Decisions (Victor, 2026-10-01): bot only on 907; human handoff by alerts to Victor's 981 in relay mode; no districts question; ask whether the sender's number is the contact number.

Flow (Spanish): nombre (first name only, for the chat), DNI (8 digits), foto DNI (private), servicios (numbers 1 to 7), días (Semana / Finde / Semana y finde), horario (Mañana / Tarde / Noche / Horario flexible), cobro (Por hora / Por servicio), precio, WhatsApp de contacto (this number or another), foto de perfil (optional), confirmar.
Values match the site filters (`includes('semana')`, `includes('finde')`, turno `horarioflexible`).

On confirm: row appended to the public sheet with `Activo` = No (hidden) and empty `Apellido`, private row (DNI etc.) to a separate private sheet, alert plus DNI photo to Victor.
Worker can type *persona* at any time; after 3 failed answers Victor gets an "atascado" alert.

Official name: Victor copies it from the DNI photo when approving (decision 2026-10-01): `aprobar N Nombres Apellidos` (last two words = apellidos; use `/` to split explicitly: `aprobar N Ana Lucía / Pérez Rojas`). This sets Nombre, Apellido, Activo, Verificado. Re-running it fixes the name without messaging the worker again.
Victor's commands (from 981 to the bot): `pendientes`, `aprobar N Nombres Apellidos`, `rechazar N motivo`, `responder 9XXXXXXXX` or `responder N` (relay: his texts go out from 907), `fin`.
24 hour rule: if the recipient hasn't written in 24h, messages are queued and the approved template `aviso_equipo` (to Victor) or `seguimiento_perfil` (to workers) is sent; queued messages go out when they reply.

Code layout: `bot/servibot/` (flow.py conversation, app.py webhook, wa.py WhatsApp client, sheets.py, store.py SQLite), `bot/tests/` (pytest, 13 tests), `bot/apps-script/Code.gs` (Google Sheets bridge pasted into the public sheet), `bot/deploy/` (setup.sh, systemd unit, Caddyfile, env template).
Secrets live only on the server in `/etc/servibot/servibot.env`, never in the repo. Data and photos in `/var/lib/servibot` (DNI photos in `privado/`, never served).
Install or update on the server (as root): `curl -fsSL https://raw.githubusercontent.com/Servipe01/servi-pe/main/bot/deploy/setup.sh | bash`. Logs: `journalctl -u servibot -f`.

## Plan

1. Go live: run setup.sh (uses 142-93-192-86.sslip.io); servi.pe DNS (GoDaddy, login unclear) can wait; private sheet plus Apps Script; Meta permanent token, app secret, webhook (`https://bot.servi.pe/webhook`), templates; publish the app.
2. Change the site's "Crea tu perfil" links from 981 to the bot number 907 once the bot is live.
3. Later, if volume grows: move from the Google Sheet to a real database.

## Open questions and risks

* The sheet is publicly readable, so anything in it (worker phone numbers, and DNI if stored there) is public. Keep DNI and private data in a separate private tab or sheet.
* Photos served from the bot server: if the server is down, profile photos on the site don't load.
* Twisso page category is still "Marketing Agency"; change it.

## Status

Bot code written and tested (not yet deployed). Meta app and number ready; number registration (6 digit PIN, Victor) to confirm. Server still empty.

## Log

* 2026-05-26: Site uploaded to GitHub (`servi.pe.html` renamed to `index.html`).
* 2026-07-16: Several updates to `index.html`.
* 2026-10-01: Context rebuilt after it was lost between sessions. Reviewed DigitalOcean, GitHub and DNS. Wrote this handbook. Chose +51 907 434 222 as the bot number. Created Servi.pe portfolio, moved Twisso page in, requested rename, created Meta app, verified the bot number, wrote the bot.
