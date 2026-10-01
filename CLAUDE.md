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
| Bot server | DigitalOcean Droplet `servi-pe-server`, IP 142.93.192.86, Ubuntu 24.04, NYC1, USD 6 per month | Created, empty, nothing deployed yet |

### Sheet columns the site reads

Matching ignores accents and capital letters.

`Nombre`, `Apellido`, `WhatsApp`, `Categorias`, `Tier` (Destacado or Basico), `Verificado`, `Foto URL`, `Precio (S/)`, `Precio Tipo` (default "Por hora"), `Turno`, `Dias disponibles`, `Rating`, `Reviews`, `Activo` (only rows with true, sí or si are shown).

Any new tool that creates profiles (the WhatsApp bot) must write rows in exactly this format.

### Config at the top of the script in index.html

`SHEET_ID`, `WA_NUMBER` (51981571118), `SHOW_COUNTER_AT` (100: the worker counter pill appears publicly at 100 workers).

## Plan

1. WhatsApp bot on the DigitalOcean server that walks workers through creating their profile (name, DNI, categories, districts, availability, price, photo) and writes the row to the Google Sheet with `Activo` empty until verified. Website needs no change.
2. Uses the official WhatsApp Cloud API (Meta Business account) on the dedicated bot number +51 907 434 222. That number must not be used in the regular WhatsApp app. The site's existing contact number 981 571 118 stays as is.
3. Later, if volume grows: move from the Google Sheet to a real database.

## Open questions and risks

* The sheet is publicly readable, so anything in it (worker phone numbers, and DNI if stored there) is public. Keep DNI and private data in a separate private tab or sheet.
* Meta Business account status: unknown.

## Status

DigitalOcean server created, empty. Bot number chosen: +51 907 434 222. Bot not started.

## Log

* 2026-05-26: Site uploaded to GitHub (`servi.pe.html` renamed to `index.html`).
* 2026-07-16: Several updates to `index.html`.
* 2026-10-01: Context rebuilt after it was lost between sessions. Reviewed DigitalOcean, GitHub and DNS. Wrote this handbook. Chose +51 907 434 222 as the bot number.
