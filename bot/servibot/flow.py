"""Conversation logic: worker signup flow, human handoff and admin commands."""
import re
import time
import uuid
from pathlib import Path

from .sheets import norm
from .wa import WAError, WINDOW_CLOSED

CATEGORIES = ["Limpieza", "Niñera", "Gasfitería", "Electricidad", "Pintura", "Carpintería", "Jardinería"]
DAYS = [("dias_semana", "Semana", "Semana"), ("dias_finde", "Finde", "Finde"),
        ("dias_ambos", "Semana y finde", "Semana y finde")]
SHIFTS = [("turno_manana", "Mañana"), ("turno_tarde", "Tarde"), ("turno_noche", "Noche"),
          ("turno_flexible", "Horario flexible")]
PRICE_TYPES = [("precio_hora", "Por hora"), ("precio_servicio", "Por servicio")]

HANDOFF_WORDS = {"persona", "humano", "asesor", "asesora", "hablar con una persona", "ayuda humana"}
RESTART_WORDS = {"reiniciar", "empezar de nuevo", "volver a empezar"}

STEP_NAMES = {
    "nombre": "nombre", "dni": "número de DNI", "dni_foto": "foto del DNI",
    "categorias": "servicios", "dias": "días", "turno": "turno", "precio_tipo": "tipo de precio",
    "precio": "precio", "contacto": "número de contacto", "contacto_otro": "otro número",
    "foto": "foto de perfil", "confirmar": "confirmación",
}

ADMIN_HELP = (
    "Comandos del equipo Servi:\n"
    "*pendientes*: perfiles por revisar\n"
    "*aprobar N Nombres Apellidos*: publica el perfil N con el nombre tal como aparece en el DNI "
    "(si hay duda con la separación usa una barra: *aprobar N Ana Lucía / Pérez Rojas*)\n"
    "*rechazar N motivo*: rechaza el perfil N (el motivo se envía al trabajador)\n"
    "*responder 9XXXXXXXX* o *responder N*: hablas tú con ese trabajador\n"
    "*fin*: terminas y el bot retoma la conversación"
)


PARTICLES = {"de", "del", "la", "las", "los", "y", "da", "di"}
NAME_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' .]+")


def nice_name(s: str) -> str:
    """'MARIA de los angeles' -> 'Maria de los Angeles'"""
    words = re.sub(r"\s+", " ", s).strip().split(" ")
    return " ".join(w.lower() if i and w.lower() in PARTICLES else w.capitalize() for i, w in enumerate(words))


def split_full_name(full: str):
    """Best guess for Peruvian names: the last two surnames are apellidos.
    'Rosa Huamán Quispe' -> ('Rosa', 'Huamán Quispe'); 'Ana Lucía Pérez Rojas' -> ('Ana Lucía', 'Pérez Rojas').
    Particles stay attached to the next word ('María de la Cruz' -> ('María', 'de la Cruz'))."""
    groups, pending = [], []
    for w in nice_name(full).split(" "):
        pending.append(w)
        if w.lower() not in PARTICLES:
            groups.append(" ".join(pending))
            pending = []
    if pending:
        groups.append(" ".join(pending))
    if len(groups) == 1:
        return groups[0], ""
    if len(groups) == 2:
        return groups[0], groups[1]
    return " ".join(groups[:-2]), " ".join(groups[-2:])


def local_number(wa_id: str) -> str:
    """51987654321 -> 987 654 321 for display."""
    d = wa_id[2:] if wa_id.startswith("51") and len(wa_id) == 11 else wa_id
    return f"{d[0:3]} {d[3:6]} {d[6:]}" if len(d) == 9 else d


class Bot:
    def __init__(self, settings, store, wa, sheets):
        self.s = settings
        self.store = store
        self.wa = wa
        self.sheets = sheets
        self.fotos_dir = Path(settings.data_dir) / "fotos"
        self.privado_dir = Path(settings.data_dir) / "privado"
        self.fotos_dir.mkdir(parents=True, exist_ok=True)
        self.privado_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ sending
    async def out(self, to: str, msg: dict):
        """Send, or hold the message and ping with a template if the 24 hour window is closed."""
        if not self.store.window_open(to):
            await self._hold(to, msg)
            return
        try:
            await self.wa.send(to, msg)
        except WAError as e:
            if e.code == WINDOW_CLOSED:
                await self._hold(to, msg)
            else:
                raise

    async def _hold(self, to: str, msg: dict):
        first = not self.store.has_queued(to)
        self.store.queue(to, msg)
        if first:
            name = self.s.admin_template if to == self.s.admin_number else self.s.worker_template
            try:
                await self.wa.send(to, {"type": "template", "name": name, "lang": self.s.template_lang})
            except WAError:
                pass  # template not approved yet: message stays queued until they write

    async def text(self, to: str, body: str):
        await self.out(to, {"type": "text", "text": body})

    async def alert_admin(self, body: str):
        await self.text(self.s.admin_number, body)

    # ------------------------------------------------------------------ entry
    async def handle(self, msg: dict):
        """msg: {id, from, type, text, reply_id, media_id, profile_name}"""
        if msg.get("id") and self.store.seen(msg["id"]):
            return
        sender = msg["from"]
        self.store.touch_inbound(sender)
        for held in self.store.pop_queued(sender):
            await self.out(sender, held)
        if sender == self.s.admin_number:
            await self.handle_admin(msg)
        else:
            await self.handle_worker(msg)

    # ------------------------------------------------------------------ worker
    def prompt(self, state: str, data: dict) -> dict:
        nombre = data.get("nombre", "")
        if state == "nombre":
            return {"type": "text", "text": (
                "¡Hola! Soy el asistente de *Servi*. Te ayudo a crear tu perfil gratis en servi.pe "
                "en unos 3 minutos.\n\nEn cualquier momento puedes escribir *persona* para hablar con "
                "nuestro equipo.\n\n¿Cómo te llamas?")}
        if state == "dni":
            return {"type": "text", "text": (
                "¿Cuál es tu número de *DNI*? (8 dígitos)\n\n"
                "Lo usamos solo para verificar tu identidad. No se muestra en tu perfil.")}
        if state == "dni_foto":
            return {"type": "text", "text": (
                "Envíanos una *foto de tu DNI* (la cara con tu foto), bien iluminada y legible. "
                "Es privada y solo la ve nuestro equipo.")}
        if state == "categorias":
            lines = "\n".join(f"{i}. {c}" for i, c in enumerate(CATEGORIES, start=1))
            return {"type": "text", "text": (
                f"¿Qué *servicios* ofreces?\n\n{lines}\n\n"
                "Escribe los números separados por coma. Por ejemplo: *1,3*")}
        if state == "dias":
            return {"type": "buttons", "text": "¿Qué *días* estás disponible?",
                    "buttons": [(bid, title) for bid, title, _ in DAYS]}
        if state == "turno":
            return {"type": "list", "text": "¿En qué *horario* trabajas?", "button": "Elegir horario",
                    "rows": SHIFTS}
        if state == "precio_tipo":
            return {"type": "buttons", "text": "¿Cómo cobras normalmente?", "buttons": PRICE_TYPES}
        if state == "precio":
            unidad = "por hora" if data.get("precio_tipo") == "Por hora" else "por servicio"
            return {"type": "text", "text": (
                f"¿Desde cuánto cobras *{unidad}*, en soles? Escribe solo el número. Por ejemplo: *25*")}
        if state == "contacto":
            return {"type": "buttons", "text": (
                f"¿Quieres usar este número (*{local_number(data.get('origen', ''))}*) como tu WhatsApp "
                "de contacto en tu perfil? Los clientes te escribirán ahí."),
                "buttons": [("contacto_si", "Sí, este número"), ("contacto_otro", "Usar otro número")]}
        if state == "contacto_otro":
            return {"type": "text", "text": "Escribe el número de WhatsApp que quieres mostrar (9 dígitos, empieza con 9)."}
        if state == "foto":
            return {"type": "buttons", "text": (
                "Por último, envía una *foto tuya* para tu perfil: de frente, con buena luz.\n\n"
                "Si prefieres, toca *Omitir por ahora* (Ojo, perfiles con foto tienen más chance "
                "de ser contratados)."),
                "buttons": [("foto_omitir", "Omitir por ahora")]}
        if state == "confirmar":
            return {"type": "buttons", "text": self.summary(data) + "\n\n¿Está todo bien?",
                    "buttons": [("confirmar_si", "Confirmar"), ("confirmar_no", "Corregir")]}
        raise ValueError(state)

    @staticmethod
    def summary(data: dict, header: str = "*Tu perfil:*") -> str:
        precio = f"S/ {data.get('precio', '')} {data.get('precio_tipo', '').lower()}"
        return (
            f"{header}\n"
            f"Nombre: {data.get('nombre', '')}\n"
            f"DNI: {data.get('dni', '')}\n"
            f"Servicios: {', '.join(data.get('categorias', []))}\n"
            f"Días: {data.get('dias', '')}\n"
            f"Horario: {data.get('turno', '')}\n"
            f"Precio: {precio}\n"
            f"WhatsApp de contacto: {local_number(data.get('contacto', ''))}\n"
            f"Foto: {'sí' if data.get('foto_url') else 'no'}"
        )

    async def handle_worker(self, msg: dict):
        sender = msg["from"]
        sess = self.store.get_session(sender)
        text = (msg.get("text") or "").strip()
        t = norm(text)

        if sess is None:
            data = {"origen": sender, "perfil_wa": msg.get("profile_name", "")}
            self.store.save_session(sender, "nombre", data)
            await self.out(sender, self.prompt("nombre", data))
            return

        state, data, retries = sess["state"], sess["data"], sess["retries"]

        if state == "humano":
            await self.forward_to_admin(sender, data, msg)
            return

        if t in HANDOFF_WORDS:
            await self.start_handoff(sender, state, data, text)
            return

        if state == "listo":
            reg = self.store.latest_registration_for(sender)
            if reg and reg["status"] == "aprobado":
                body = "Tu perfil ya está publicado en servi.pe. Si necesitas cambiar algo, escribe *persona*."
            elif reg and reg["status"] == "rechazado":
                body = "Tu perfil no fue aprobado. Escribe *persona* para hablar con nuestro equipo."
            else:
                body = "Tu perfil está en revisión. Te avisamos por aquí apenas esté publicado."
            await self.text(sender, body)
            return

        if t in RESTART_WORDS:
            data = {"origen": data.get("origen", sender), "perfil_wa": data.get("perfil_wa", "")}
            self.store.save_session(sender, "nombre", data)
            await self.out(sender, self.prompt("nombre", data))
            return

        nxt, error = await self.step(state, data, msg)
        if error:
            retries += 1
            self.store.save_session(sender, state, data, retries)
            if retries >= 2:
                error += "\n\nSi prefieres, escribe *persona* y alguien de nuestro equipo te ayuda."
            await self.text(sender, error)
            if retries == 3:
                await self.alert_admin(
                    f"Aviso: {data.get('nombre') or local_number(sender)} ({local_number(sender)}) parece "
                    f"atascado en el paso: {STEP_NAMES.get(state, state)}.\n"
                    f"Para ayudarle escribe: *responder {local_number(sender).replace(' ', '')}*")
            return

        if nxt == "guardar":
            await self.finish(sender, data)
            return
        self.store.save_session(sender, nxt, data, 0)
        await self.out(sender, self.prompt(nxt, data))

    async def step(self, state: str, data: dict, msg: dict):
        """Validate the answer for `state`. Returns (next_state, error_text)."""
        text = (msg.get("text") or "").strip()
        reply = msg.get("reply_id") or ""

        if state == "nombre":
            clean = re.sub(r"\s+", " ", text).strip(" .")
            if not (2 <= len(clean) <= 40) or not NAME_RE.fullmatch(clean):
                return None, "Escribe tu nombre solo con letras. Por ejemplo: *Rosa*"
            data["nombre"] = nice_name(clean)
            return "dni", None

        if state == "dni":
            digits = re.sub(r"\D", "", text)
            if len(digits) != 8:
                return None, "El DNI debe tener 8 dígitos. Escríbelo solo con números."
            data["dni"] = digits
            return "dni_foto", None

        if state == "dni_foto":
            if msg.get("type") != "image" or not msg.get("media_id"):
                return None, "Necesitamos una *foto* de tu DNI. Usa el clip o la cámara de WhatsApp para enviarla."
            content, _ = await self.wa.download_media(msg["media_id"])
            fname = f"{data['origen']}_{int(time.time())}.jpg"
            (self.privado_dir / fname).write_bytes(content)
            data["dni_foto"] = fname
            return "categorias", None

        if state == "categorias":
            nums = [int(n) for n in re.findall(r"\d+", text)]
            picked = []
            for n in nums:
                if 1 <= n <= len(CATEGORIES) and CATEGORIES[n - 1] not in picked:
                    picked.append(CATEGORIES[n - 1])
            if not picked:
                # also accept category names typed out
                picked = [c for c in CATEGORIES if norm(c) in norm(text)]
            if not picked:
                return None, "No entendí. Escribe los números de tus servicios separados por coma. Por ejemplo: *1,3*"
            data["categorias"] = picked
            return "dias", None

        if state == "dias":
            match = next((value for bid, title, value in DAYS if reply == bid or norm(text) == norm(title)), None)
            if not match:
                return None, "Elige una opción con los botones: *Semana*, *Finde* o *Semana y finde*."
            data["dias"] = match
            return "turno", None

        if state == "turno":
            match = next((title for rid, title in SHIFTS if reply == rid or norm(text) == norm(title)), None)
            if not match and "flex" in norm(text):
                match = "Horario flexible"
            if not match:
                return None, "Elige tu horario en la lista: Mañana, Tarde, Noche u Horario flexible."
            data["turno"] = match
            return "precio_tipo", None

        if state == "precio_tipo":
            match = next((title for bid, title in PRICE_TYPES if reply == bid or norm(text) == norm(title)), None)
            if not match:
                if "hora" in norm(text):
                    match = "Por hora"
                elif "servicio" in norm(text) or "trabajo" in norm(text):
                    match = "Por servicio"
            if not match:
                return None, "Elige con los botones: *Por hora* o *Por servicio*."
            data["precio_tipo"] = match
            return "precio", None

        if state == "precio":
            m = re.search(r"\d+(?:[.,]\d+)?", text)
            value = round(float(m.group().replace(",", "."))) if m else 0
            if not (1 <= value <= 5000):
                return None, "Escribe solo el monto en soles, con números. Por ejemplo: *25*"
            data["precio"] = str(value)
            return "contacto", None

        if state == "contacto":
            if reply == "contacto_si" or norm(text) in {"si", "sí", "si, este numero"}:
                data["contacto"] = data["origen"]
                return "foto", None
            if reply == "contacto_otro" or "otro" in norm(text):
                return "contacto_otro", None
            return None, "Elige con los botones: *Sí, este número* o *Usar otro número*."

        if state == "contacto_otro":
            digits = re.sub(r"\D", "", text)
            if digits.startswith("51") and len(digits) == 11:
                digits = digits[2:]
            if len(digits) != 9 or not digits.startswith("9"):
                return None, "El número debe tener 9 dígitos y empezar con 9. Por ejemplo: *987654321*"
            data["contacto"] = "51" + digits
            return "foto", None

        if state == "foto":
            if reply == "foto_omitir" or "omitir" in norm(text):
                data["foto_url"] = ""
                return "confirmar", None
            if msg.get("type") != "image" or not msg.get("media_id"):
                return None, "Envía una *foto* tuya o toca *Omitir por ahora* (Ojo, perfiles con foto tienen más chance de ser contratados)."
            content, _ = await self.wa.download_media(msg["media_id"])
            fname = f"{uuid.uuid4().hex}.jpg"
            (self.fotos_dir / fname).write_bytes(content)
            data["foto_file"] = fname
            data["foto_url"] = f"{self.s.public_base_url.rstrip('/')}/fotos/{fname}"
            return "confirmar", None

        if state == "confirmar":
            if reply == "confirmar_si" or norm(text) in {"confirmar", "si", "sí", "ok"}:
                return "guardar", None
            if reply == "confirmar_no" or "corregir" in norm(text):
                data.clear()
                return "nombre", None
            return None, "Toca *Confirmar* si todo está bien, o *Corregir* para empezar de nuevo."

        return None, "No entendí. Escribe *persona* para hablar con nuestro equipo."

    async def finish(self, sender: str, data: dict):
        reg_id = self.store.add_registration(sender, data)
        self.store.save_session(sender, "listo", data, 0)
        public = {
            "ID": str(reg_id), "Nombre": data["nombre"], "Apellido": "",
            "WhatsApp": data["contacto"], "Categorias": ", ".join(data["categorias"]),
            "Tier": "Basico", "Verificado": "No", "Foto URL": data.get("foto_url", ""),
            "Precio (S/)": data["precio"], "Precio Tipo": data["precio_tipo"], "Turno": data["turno"],
            "Dias disponibles": data["dias"], "Rating": "0", "Reviews": "0", "Activo": "No",
        }
        private = {
            "ID": str(reg_id), "Fecha": time.strftime("%Y-%m-%d %H:%M"), "Nombre": data["nombre"],
            "Apellido": "", "DNI": data["dni"], "WhatsApp origen": sender,
            "WhatsApp contacto": data["contacto"], "Foto DNI (archivo en servidor)": data.get("dni_foto", ""),
            "Estado": "Pendiente",
        }
        sheet_note = ""
        try:
            await self.sheets.append(public, private)
        except Exception as e:  # keep the registration even if Google is down
            sheet_note = f"\n\nOJO: no se pudo guardar en la hoja ({e}). Está guardado en el bot."
        await self.text(sender, (
            f"¡Listo, {data['nombre']}! Recibimos tu perfil. Lo revisamos y te avisamos por aquí "
            "cuando esté publicado en servi.pe (normalmente en menos de 24 horas)."))
        await self.alert_admin(
            f"Nuevo perfil #{reg_id}\n\n{self.summary(data, header='*Datos:*')}\n\n"
            "Revisa el DNI y para publicarlo escribe el nombre tal como aparece en él:\n"
            f"*aprobar {reg_id} Nombres Apellidos*\n"
            f"Para hablar con la persona: *responder {reg_id}*{sheet_note}")
        await self.send_private_photo(data.get("dni_foto"), self.privado_dir, f"DNI del perfil #{reg_id}")
        await self.send_private_photo(data.get("foto_file"), self.fotos_dir, f"Foto del perfil #{reg_id}")

    async def send_private_photo(self, fname, folder: Path, caption: str):
        if not fname or not (folder / fname).exists():
            return
        try:
            media_id = await self.wa.upload_media((folder / fname).read_bytes(), "image/jpeg")
            await self.out(self.s.admin_number, {"type": "image", "media_id": media_id, "caption": caption})
        except Exception:
            pass

    # ------------------------------------------------------------------ handoff
    async def start_handoff(self, sender: str, state: str, data: dict, last_text: str):
        data["_volver_a"] = state
        self.store.save_session(sender, "humano", data, 0)
        await self.text(sender, "Listo, le aviso a nuestro equipo. Te escribirán por aquí en breve.")
        quien = data.get("nombre") or data.get("perfil_wa") or "Alguien"
        await self.alert_admin(
            f"{quien} ({local_number(sender)}) pide hablar con una persona.\n"
            f"Paso: {STEP_NAMES.get(state, state)}\nÚltimo mensaje: \"{last_text}\"\n\n"
            f"Para responderle escribe: *responder {local_number(sender).replace(' ', '')}*")

    async def forward_to_admin(self, sender: str, data: dict, msg: dict):
        quien = data.get("nombre") or data.get("perfil_wa") or local_number(sender)
        content = msg.get("text") or ("[foto]" if msg.get("type") == "image" else f"[{msg.get('type')}]")
        hint = "" if self.store.get("relay") == sender else \
            f"\n(Para contestarle: *responder {local_number(sender).replace(' ', '')}*)"
        await self.alert_admin(f"[{quien}] {content}{hint}")
        if msg.get("type") == "image" and msg.get("media_id"):
            try:
                content_bytes, mime = await self.wa.download_media(msg["media_id"])
                media_id = await self.wa.upload_media(content_bytes, mime)
                await self.out(self.s.admin_number, {"type": "image", "media_id": media_id, "caption": quien})
            except Exception:
                pass

    # ------------------------------------------------------------------ admin
    def resolve_target(self, arg: str):
        digits = re.sub(r"\D", "", arg)
        if not digits:
            return None
        if len(digits) <= 6:
            reg = self.store.get_registration(int(digits))
            return reg["wa_id"] if reg else None
        if len(digits) == 9:
            return "51" + digits
        return digits

    async def handle_admin(self, msg: dict):
        admin = self.s.admin_number
        text = (msg.get("text") or "").strip()
        words = text.split()
        cmd = norm(words[0]) if words else ""
        relay = self.store.get("relay")

        if cmd == "aprobar" and len(words) > 1 and words[1].isdigit():
            reg = self.store.get_registration(int(words[1]))
            if not reg:
                await self.text(admin, f"No encuentro el perfil #{words[1]}.")
                return
            full = " ".join(words[2:]).strip()
            if "/" in full:
                nombre, apellido = (nice_name(x) for x in full.split("/", 1))
            else:
                nombre, apellido = split_full_name(full) if full else ("", "")
            if not nombre or not apellido or not NAME_RE.fullmatch(nombre + " " + apellido):
                await self.text(admin, (
                    "Escribe también el nombre tal como aparece en el DNI. Por ejemplo:\n"
                    f"*aprobar {reg['id']} Rosa Huamán Quispe*"))
                return
            reg["data"]["nombre_dni"], reg["data"]["apellido_dni"] = nombre, apellido
            self.store.update_registration_data(reg["id"], reg["data"])
            self.store.set_registration_status(reg["id"], "aprobado")
            try:
                await self.sheets.set_status(
                    reg["id"],
                    {"Nombre": nombre, "Apellido": apellido, "Activo": "Sí", "Verificado": "Sí"},
                    {"Nombre": nombre, "Apellido": apellido, "Estado": "Aprobado"})
                note = f"Ya aparece en servi.pe como *{nombre} {apellido[:1]}.*"
            except Exception as e:
                note = f"OJO: no se pudo actualizar la hoja ({e}). Márcalo como Activo a mano."
            await self.text(admin, (
                f"Perfil #{reg['id']} aprobado.\nNombre: {nombre}\nApellidos: {apellido}\n{note}\n\n"
                f"Si la separación no es correcta, vuelve a escribir: *aprobar {reg['id']} Nombres / Apellidos*"))
            if reg["data"].get("_avisado") != "1":
                reg["data"]["_avisado"] = "1"
                self.store.update_registration_data(reg["id"], reg["data"])
                await self.text(reg["wa_id"], (
                    f"¡Buenas noticias, {reg['data'].get('nombre', '')}! Tu perfil ya está publicado en "
                    "servi.pe. Los clientes te escribirán directamente a tu WhatsApp."))
            return

        if cmd == "rechazar" and len(words) > 1 and words[1].isdigit():
            reg = self.store.get_registration(int(words[1]))
            if not reg:
                await self.text(admin, f"No encuentro el perfil #{words[1]}.")
                return
            self.store.set_registration_status(reg["id"], "rechazado")
            try:
                await self.sheets.set_status(reg["id"], {"Activo": "No"}, {"Estado": "Rechazado"})
            except Exception:
                pass
            motivo = " ".join(words[2:])
            if motivo:
                await self.text(reg["wa_id"], f"No pudimos aprobar tu perfil: {motivo}\nEscribe *persona* si tienes dudas.")
            await self.text(admin, f"Perfil #{reg['id']} rechazado." + ("" if motivo else " No se avisó al trabajador."))
            return

        if cmd == "pendientes":
            pend = self.store.pending_registrations()
            if not pend:
                await self.text(admin, "No hay perfiles pendientes.")
            else:
                lines = [f"#{r['id']} {r['data'].get('nombre', '')}: "
                         f"{', '.join(r['data'].get('categorias', []))}" for r in pend]
                await self.text(admin, "Pendientes:\n" + "\n".join(lines) + "\n\nPara publicar: *aprobar N Nombres Apellidos* (como en el DNI).")
            return

        if cmd == "responder" and len(words) > 1:
            target = self.resolve_target(words[1])
            if not target or target == admin:
                await self.text(admin, "No encuentro a esa persona. Usa el número de 9 dígitos o el número de perfil.")
                return
            sess = self.store.get_session(target) or {"state": "nombre", "data": {"origen": target}, "retries": 0}
            if sess["state"] != "humano":
                sess["data"]["_volver_a"] = sess["state"]
                self.store.save_session(target, "humano", sess["data"], 0)
            self.store.set("relay", target)
            await self.text(admin, (
                f"Ahora hablas con {local_number(target)}. Todo lo que escribas le llegará desde el número de "
                "Servi. Escribe *fin* para devolverle la conversación al bot."))
            await self.text(target, "Hola, te escribe el equipo de Servi.")
            return

        if cmd == "fin":
            if not relay:
                await self.text(admin, "No estabas hablando con nadie.")
                return
            self.store.set("relay", None)
            sess = self.store.get_session(relay)
            if sess:
                back = sess["data"].pop("_volver_a", "nombre")
                self.store.save_session(relay, back, sess["data"], 0)
                if back == "listo":
                    await self.text(relay, "Gracias por escribirnos. Cualquier cosa, aquí estamos.")
                else:
                    await self.text(relay, "Gracias. Sigamos con tu perfil.")
                    await self.out(relay, self.prompt(back, sess["data"]))
            await self.text(admin, f"Listo. El bot retoma la conversación con {local_number(relay)}.")
            return

        if relay:
            if msg.get("type") == "text" and text:
                await self.text(relay, text)
            else:
                await self.text(admin, "Por ahora solo puedo reenviar mensajes de texto.")
            return

        await self.text(admin, ADMIN_HELP)
