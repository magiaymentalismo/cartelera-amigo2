import os, json, re, requests, time, logging
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ====================== CONFIG ======================
URL = "https://magiaymentalismo.github.io/Atrapalo_clean/?v=1632222"
UA  = {"User-Agent": "Mozilla/5.0 (X11; Linux) AppleWebKit/537.36 Chrome/123 Safari/537.36"}
TZ  = ZoneInfo("Europe/Madrid")
TELEGRAM_LIMIT = 4096
CACHE_TTL = 60  # segundos
STATE_FILE = Path("state.json")

# ✅ NO mostrar en el bot (pero sí existe en la web)
EXCLUDE_EVENTS_FROM_BOT = {"Juanma"}

# ⚠️ Seguridad: primero intenta leer TELEGRAM_TOKEN del entorno.
# Si no existe, usa el token pegado aquí como respaldo.
# ¡No subas este archivo con el token al repositorio público!
TOKEN_FALLBACK = "8566367368:AAG4FTbn3uezMbtBFxMH7E2eEMeH4fsTbQ0"

# Logging básico
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Menos ruido de httpx y apscheduler en consola
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.INFO)

# ====================== CACHE ======================
_cache: Tuple[float, Dict[str, Any]] | None = None

def _now() -> float:
    return time.monotonic()

def _is_excluded(event_name: Optional[str]) -> bool:
    return bool(event_name) and event_name in EXCLUDE_EVENTS_FROM_BOT

# ====================== UTILS ======================
def _normalize_int(x) -> Optional[int]:
    if x in (None, "", "—", "-", "N/A", "NA"):
        return None
    try:
        s = str(x).replace(".", "").replace(",", "")
        return int(s)
    except Exception:
        return None

def _split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> List[str]:
    """Divide mensajes largos para no pasar el límite de Telegram."""
    if len(text) <= limit:
        return [text]
    parts, chunk = [], []
    total = 0
    for line in text.splitlines(keepends=True):
        if total + len(line) > limit:
            parts.append("".join(chunk))
            chunk, total = [line], len(line)
        else:
            chunk.append(line)
            total += len(line)
    if chunk:
        parts.append("".join(chunk))
    return parts

def _extract_payload_from_html(html: str) -> Dict[str, Any]:
    """Busca el JSON principal (PAYLOAD) en el HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # 1) <script id="PAYLOAD" type="application/json">...</script>
    tag = soup.find("script", id="PAYLOAD")
    if tag and tag.string:
        return json.loads(tag.string)

    # 2) <script data-payload="true">...</script>
    tag = soup.find("script", attrs={"data-payload": True})
    if tag and tag.string:
        return json.loads(tag.string)

    # 3) window.PAYLOAD = {...};
    for s in soup.find_all("script"):
        txt = s.string or ""
        m = re.search(r"window\.PAYLOAD\s*=\s*(\{.*?\})\s*;?", txt, flags=re.S)
        if m:
            return json.loads(m.group(1))

    raise ValueError("No encontré el PAYLOAD en el HTML.")

def fetch_payload(force: bool = False) -> Dict[str, Any]:
    """Descarga y cachea los datos de la cartelera."""
    global _cache
    if (not force) and _cache and (_now() - _cache[0] < CACHE_TTL):
        return _cache[1]

    try:
        r = requests.get(URL, headers=UA, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        if _cache:
            logger.warning("HTTP error, usando cache: %s", e)
            return _cache[1]
        raise RuntimeError(f"HTTP error: {e}") from e

    try:
        data = _extract_payload_from_html(r.text)
    except Exception as e:
        if _cache:
            logger.warning("Error parseando payload, usando cache: %s", e)
            return _cache[1]
        raise RuntimeError(f"No pude parsear el payload: {e}") from e

    _cache = (_now(), data)
    return data

def _safe_pct(vendidas: Optional[int], cap: Optional[int]) -> Optional[int]:
    if vendidas is None or cap in (None, 0):
        return None
    try:
        return round((vendidas / cap) * 100)
    except Exception:
        return None

def _fmt_extra(vendidas, cap, stock) -> str:
    parts = []
    if cap is not None and vendidas is not None:
        pct = _safe_pct(vendidas, cap)
        if pct is not None:
            parts.append(f"{vendidas}/{cap} ({pct}%)")
        else:
            parts.append(f"{vendidas}/{cap}")
    elif vendidas is not None:
        parts.append(f"vendidas {vendidas}")
    if stock not in (None, ""):
        parts.append(f"quedan {stock}")
    return (" · " + " ".join(parts)) if parts else ""

def _reply_long(update: Update, text: str):
    """Sincrónico para usar dentro de async con 'await'."""
    async def _inner():
        for part in _split_for_telegram(text):
            if getattr(update, "callback_query", None):
                await update.callback_query.message.reply_text(part)
            else:
                await update.message.reply_text(part)
    return _inner()

# ================== HELPERS SOBRE EL PAYLOAD ================== #
def _iter_all_rows(data: Dict[str, Any]):
    """
    Itera sobre TODAS las filas de todos los eventos,
    independientemente de si vienen como:
      - evento["table"]["rows"]
      - evento["proximas"]["table"]["rows"] + evento["pasadas"]["table"]["rows"]
    Rinde: (nombre_evento, fila)
    """
    eventos = data.get("eventos") or {}
    for nombre, info in eventos.items():
        if _is_excluded(nombre):
            continue
        if not isinstance(info, dict):
            continue

        # Caso nuevo: proximas / pasadas
        if "proximas" in info or "pasadas" in info:
            for sec_name in ("proximas", "pasadas"):
                sec = info.get(sec_name) or {}
                table = (sec.get("table") or {})
                rows = table.get("rows") or []
                for r in rows:
                    yield nombre, r
        else:
            # Caso legacy: tabla plana
            table = (info.get("table") or {})
            rows = table.get("rows") or []
            for r in rows:
                yield nombre, r

def _iter_flat_functions(data: Dict[str, Any]):
    """
    Versión normalizada de todas las funciones.
    Rinde dicts con key estable para comparar ventas.
    """
    for evento, r in _iter_all_rows(data):
        # r = [FechaLabel, Hora, Vendidas, FechaISO, Capacidad?, Stock?, Abono?]
        fecha_label = r[0] if len(r) > 0 else ""
        hora        = r[1] if len(r) > 1 else ""
        vendidas    = _normalize_int(r[2] if len(r) > 2 else None)
        fecha_iso   = r[3] if len(r) > 3 else ""
        cap         = _normalize_int(r[4] if len(r) > 4 else None)
        stock       = _normalize_int(r[5] if len(r) > 5 else None)

        key = f"{evento}::{fecha_iso}::{hora}"
        yield {
            "key": key,
            "evento": evento,
            "fecha_label": fecha_label,
            "hora": hora,
            "fecha_iso": fecha_iso,
            "vendidas": vendidas,
            "cap": cap,
            "stock": stock,
        }

def _iter_upcoming_functions(data: Dict[str, Any]):
    """
    Versión que solo itera sobre funciones PRÓXIMAS (no pasadas).
    Esto evita alertas falsas cuando shows cierran y se mueven a 'pasadas'.
    """
    eventos = data.get("eventos") or {}
    for nombre, info in eventos.items():
        if _is_excluded(nombre):
            continue
        if not isinstance(info, dict):
            continue

        # Solo procesamos la sección "proximas"
        proximas = info.get("proximas") or {}
        table = proximas.get("table") or {}
        rows = table.get("rows") or []

        for r in rows:
            fecha_label = r[0] if len(r) > 0 else ""
            hora        = r[1] if len(r) > 1 else ""
            vendidas    = _normalize_int(r[2] if len(r) > 2 else None)
            fecha_iso   = r[3] if len(r) > 3 else ""
            cap         = _normalize_int(r[4] if len(r) > 4 else None)
            stock       = _normalize_int(r[5] if len(r) > 5 else None)

            key = f"{nombre}::{fecha_iso}::{hora}"
            yield {
                "key": key,
                "evento": nombre,
                "fecha_label": fecha_label,
                "hora": hora,
                "fecha_iso": fecha_iso,
                "vendidas": vendidas,
                "cap": cap,
                "stock": stock,
            }

def _get_rows_for_event_view(ev: Dict[str, Any], top: int = 5) -> List[list]:
    """
    Devuelve las filas que se usan para mostrar en /status o por evento.
    PREFERIMOS 'proximas'. Si no hay, usamos 'pasadas'.
    Soporta payload nuevo (proximas/pasadas) y legacy (table).
    """
    if not isinstance(ev, dict):
        return []

    # Nuevo formato
    if "proximas" in ev:
        rows = (((ev.get("proximas") or {}).get("table") or {}).get("rows") or [])
        return rows[:top] if top else rows

    # Formato antiguo
    rows = (((ev.get("table") or {}).get("rows") or []))
    return rows[:top] if top else rows

def format_resume(data: Dict[str, Any], evento: Optional[str] = None, top: int = 5) -> str:
    eventos = data.get("eventos", {})
    gen_str = data.get("generated_at") or data.get("generatedAt") or datetime.now(tz=TZ).isoformat()
    try:
        gen_dt = datetime.fromisoformat(gen_str.replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        gen_dt = datetime.now(tz=TZ)
    header = f"🪄 Cartelera (actualizado {gen_dt:%d/%m %H:%M})"

    lines = [header]
    keys = [k for k in eventos.keys() if not _is_excluded(k)]

    # Filtro por nombre de evento (si el usuario pide "Juanma", no mostramos)
    if evento:
        wanted = evento.casefold()
        keys = [k for k in keys if wanted in k.casefold()]
        if not keys:
            return f"No encontré un evento que contenga “{evento}”."

    for k in keys:
        ev = eventos.get(k) or {}
        rows = _get_rows_for_event_view(ev, top=top)
        if not rows:
            continue
        lines.append(f"\n— {k} —")
        for r in rows:
            fecha_label = r[0] if len(r) > 0 else ""
            hora        = r[1] if len(r) > 1 else ""
            vendidas    = _normalize_int(r[2] if len(r) > 2 else None)
            cap         = _normalize_int(r[4] if len(r) > 4 else None)
            stock       = _normalize_int(r[5] if len(r) > 5 else None)
            extra = _fmt_extra(vendidas, cap, stock)
            lines.append(f"• {fecha_label} {hora}{extra}")

    return "\n".join(lines) if len(lines) > 1 else "Sin funciones."

# ====================== ESTADO ======================
def _load_state():
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    else:
        raw = {}

    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("subscribers", [])
    raw.setdefault("counts", {})
    return raw

def _save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("No pude guardar state.json: %s", e)

# ====================== COMANDOS ======================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🎭 Disfrutá", callback_data="evento:Disfruta"),
            InlineKeyboardButton("😱 Miedo", callback_data="evento:Miedo"),
        ],
        [
            InlineKeyboardButton("🕵️‍♂️ Escondido", callback_data="evento:Escondido"),
            InlineKeyboardButton("🪄 Todos", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🔔 Suscribirme", callback_data="subscribe"),
            InlineKeyboardButton("🔕 Desuscribirme", callback_data="unsubscribe"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎩 ¡Hola! Soy el bot de la cartelera.\n"
        "¿De qué show querés saber hoy?",
        reply_markup=reply_markup,
    )

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data = fetch_payload()
        msg = format_resume(data, evento=None, top=10)
        await _reply_long(update, msg)
    except Exception as e:
        await update.message.reply_text(f"Error leyendo datos: {e}")

async def evento_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        q = " ".join(ctx.args).strip()
        if not q:
            await update.message.reply_text("Uso: /evento <texto>")
            return
        data = fetch_payload()
        msg = format_resume(data, evento=q, top=20)
        await _reply_long(update, msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def find_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not ctx.args:
            await update.message.reply_text("Uso: /find YYYY-MM-DD")
            return
        wanted = ctx.args[0]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", wanted):
            await update.message.reply_text("Formato inválido. Usa YYYY-MM-DD.")
            return
        data = fetch_payload()
        results = []
        for k, r in _iter_all_rows(data):
            if len(r) > 3 and r[3] == wanted:
                results.append((k, r))
        if not results:
            await update.message.reply_text("No hay funciones ese día.")
            return
        lines = [f"🎫 Funciones el {wanted}:"]
        for (k, r) in results:
            fecha_label = r[0] if len(r) > 0 else ""
            hora        = r[1] if len(r) > 1 else ""
            vendidas    = _normalize_int(r[2] if len(r) > 2 else None)
            cap         = _normalize_int(r[4] if len(r) > 4 else None)
            stock       = _normalize_int(r[5] if len(r) > 5 else None)
            extra = _fmt_extra(vendidas, cap, stock)
            lines.append(f"• {k}: {fecha_label} {hora}{extra}")
        await _reply_long(update, "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def lowstock_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        threshold = None
        if ctx.args:
            try:
                threshold = int(ctx.args[0])
            except Exception:
                threshold = None
        threshold = threshold or 10

        data = fetch_payload()
        lines = [f"⚠️ Funciones con ≤ {threshold} entradas:"]
        count = 0
        for k, r in _iter_all_rows(data):
            stock = _normalize_int(r[5] if len(r) > 5 else None)
            if stock is not None and stock <= threshold and stock >= 0:
                fecha_label = r[0] if len(r) > 0 else ""
                hora        = r[1] if len(r) > 1 else ""
                lines.append(f"• {k}: {fecha_label} {hora} · quedan {stock}")
                count += 1
        if count == 0:
            await update.message.reply_text("No hay funciones con pocas entradas.")
        else:
            await _reply_long(update, "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def soldout_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data = fetch_payload()
        lines = ["⛔ Funciones agotadas:"]
        count = 0
        for k, r in _iter_all_rows(data):
            stock = _normalize_int(r[5] if len(r) > 5 else None)
            if stock == 0:
                fecha_label = r[0] if len(r) > 0 else ""
                hora        = r[1] if len(r) > 1 else ""
                lines.append(f"• {k}: {fecha_label} {hora} · AGOTADO")
                count += 1
        if count == 0:
            await update.message.reply_text("No hay funciones agotadas.")
        else:
            await _reply_long(update, "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def raw_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data = fetch_payload()
        keys = list(data.keys())
        eventos = [e for e in (data.get("eventos") or {}).keys() if not _is_excluded(e)]
        gen = data.get("generated_at") or data.get("generatedAt")
        msg = (
            "🧪 RAW\n"
            f"keys: {keys}\n"
            f"generated_at: {gen}\n"
            f"eventos: {', '.join(eventos) if eventos else '(ninguno)'}"
        )
        await _reply_long(update, msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# ====================== SUSCRIPCIÓN & ALERTAS ======================
async def subscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = _load_state()
    if chat_id not in state["subscribers"]:
        state["subscribers"].append(chat_id)
        _save_state(state)
        await update.message.reply_text("✅ Suscripción activa. Te avisaré cuando suban o bajen las ventas.")
    else:
        await update.message.reply_text("Ya estabas suscrito ✅")

async def unsubscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = _load_state()
    if chat_id in state["subscribers"]:
        state["subscribers"].remove(chat_id)
        _save_state(state)
        await update.message.reply_text("❌ Suscripción cancelada. Ya no enviaré alertas.")
    else:
        await update.message.reply_text("No estabas suscrito.")

async def poll_and_notify(context):
    """Job que se ejecuta cada X minutos: compara vendidas y avisa."""
    try:
        data = fetch_payload()
    except Exception as e:
        logger.warning("No pude obtener payload en poll: %s", e)
        return

    state = _load_state()
    last_counts: Dict[str, int] = state.get("counts", {}) or {}
    changes = []

    # Solo funciones próximas (y excluyendo Juanma por _iter_upcoming_functions)
    current_functions = list(_iter_upcoming_functions(data))

    for f in current_functions:
        k = f["key"]
        v = f["vendidas"] or 0
        prev = last_counts.get(k)

        if prev is None:
            # Primera vez: inicializa sin avisar
            last_counts[k] = v
            continue

        if v > prev:
            diff = v - prev
            extra = _fmt_extra(v, f["cap"], f["stock"])
            changes.append(
                f"📈 *Nuevas ventas* (+{diff}) — {f['evento']}\n"
                f"• {f['fecha_label']} {f['hora']}{extra}"
            )
        elif v < prev:
            diff = prev - v
            extra = _fmt_extra(v, f["cap"], f["stock"])
            changes.append(
                f"📉 *Bajaron las vendidas* (-{diff}) — {f['evento']}\n"
                f"• {f['fecha_label']} {f['hora']}{extra}"
            )

        last_counts[k] = v

    # Limpia keys de funciones que ya no existan (y también purga Juanma viejo si quedó)
    current_keys = {f["key"] for f in current_functions}
    for k in list(last_counts.keys()):
        if k not in current_keys:
            last_counts.pop(k, None)

    # Guarda estado
    state["counts"] = last_counts
    _save_state(state)

    # Notifica
    if changes and state["subscribers"]:
        text = "🔔 *Actualizaciones de cartelera*\n\n" + "\n\n".join(changes)
        for chat_id in state["subscribers"]:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("No pude enviar alerta a %s: %s", chat_id, e)

# ====================== BOTONES (callback) ======================
async def button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "status":
        data_json = fetch_payload()
        msg = format_resume(data_json, evento=None, top=10)
        await _reply_long(update, msg)
        return

    if data == "subscribe":
        fake_update = Update(update.update_id, message=query.message)  # para reutilizar lógica
        await subscribe_cmd(fake_update, ctx)
        return

    if data == "unsubscribe":
        fake_update = Update(update.update_id, message=query.message)
        await unsubscribe_cmd(fake_update, ctx)
        return

    if data.startswith("evento:"):
        nombre = data.split(":", 1)[1]
        if _is_excluded(nombre):
            await query.edit_message_text("Ese evento no se muestra en el bot 🙂")
            return
        data_json = fetch_payload()
        msg = format_resume(data_json, evento=nombre, top=20)
        await _reply_long(update, msg)
        return

    await query.edit_message_text("No entendí tu selección 😅")

# ====================== MAIN ======================
def main():
    # 1) Usa variable de entorno si existe
    token = os.getenv("TELEGRAM_TOKEN")
    # 2) Si no, usa el respaldo pegado aquí (⚠️ no subir a repositorios públicos)
    if not token:
        token = TOKEN_FALLBACK

    if not token:
        raise SystemExit("❌ Falta TOKEN. Configura TELEGRAM_TOKEN o edita TOKEN_FALLBACK.")

    app = ApplicationBuilder().token(token).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("evento", evento_cmd))
    app.add_handler(CommandHandler("find", find_cmd))
    app.add_handler(CommandHandler("lowstock", lowstock_cmd))
    app.add_handler(CommandHandler("soldout", soldout_cmd))
    app.add_handler(CommandHandler("raw", raw_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))

    # Botones
    app.add_handler(CallbackQueryHandler(button_callback))

    # JobQueue: revisa cada 2 minutos
    app.job_queue.run_repeating(poll_and_notify, interval=120, first=5)

    # Manejador de errores silencioso
    async def on_error(update, context):
        logger.warning("Error: %s", context.error)
    app.add_error_handler(on_error)

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()