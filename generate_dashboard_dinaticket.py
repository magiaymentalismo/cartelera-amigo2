#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


# ===================== CONFIG ===================== #

DINATICKET_EVENTS = {
    "Escondido": "https://www.dinaticket.com/es/provider/20073/event/4919204",
    "Oniria": "https://www.dinaticket.com/es/provider/20073/event/4940326",
}

ONEBOX_EVENTS = {
    "Escalera": "https://entradas.laescaleradejacob.es/laescaleradejacob/events/56109",
}

ONEBOX_FALLBACK_SELECTS = {
    "https://entradas.laescaleradejacob.es/laescaleradejacob/events/56109": [
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2877829",
            "fecha_iso": "2026-06-06",
            "hora": "23:00",
        },
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2877830",
            "fecha_iso": "2026-06-13",
            "hora": "23:00",
        },
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2889698",
            "fecha_iso": "2026-06-20",
            "hora": "23:00",
        },
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2904421",
            "fecha_iso": "2026-06-07",
            "hora": "21:00",
        },
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2904422",
            "fecha_iso": "2026-06-14",
            "hora": "21:00",
        },
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2904423",
            "fecha_iso": "2026-06-21",
            "hora": "21:00",
        },
        {
            "url": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2904452",
            "fecha_iso": "2026-06-19",
            "hora": "20:00",
        },
    ],
}

FEVER_URLS = {
    "Miedo": "https://feverup.com/m/290561",
    "Disfruta": "https://feverup.com/m/159767",
}

ABONO_URL = "https://compras.abonoteatro.com/?pagename=espectaculo&eventid=23816"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
    )
}

TZ = ZoneInfo("Europe/Madrid")

TEMPLATE_PATH = Path("template.html")
MANIFEST_PATH = Path("manifest.json")
SW_PATH = Path("sw.js")
DOCS_DIR = Path("docs")
ONEBOX_CACHE_PATH = DOCS_DIR / "onebox_cache.json"


MESES = {
    "Ene.": "01", "Ene": "01",
    "Feb.": "02", "Feb": "02",
    "Mar.": "03", "Mar": "03",
    "Abr.": "04", "Abr": "04",
    "May.": "05", "May": "05",
    "Jun.": "06", "Jun": "06",
    "Jul.": "07", "Jul": "07",
    "Ago.": "08", "Ago": "08",
    "Sep.": "09", "Sep": "09",
    "Oct.": "10", "Oct": "10",
    "Nov.": "11", "Nov": "11",
    "Dic.": "12", "Dic": "12",
}

MESES_LARGO = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}

MESES_ES = {
    "ene": "01", "enero": "01",
    "feb": "02", "febrero": "02",
    "mar": "03", "marzo": "03",
    "abr": "04", "abril": "04",
    "may": "05", "mayo": "05",
    "jun": "06", "junio": "06",
    "jul": "07", "julio": "07",
    "ago": "08", "agosto": "08",
    "sep": "09", "sept": "09", "septiembre": "09",
    "oct": "10", "octubre": "10",
    "nov": "11", "noviembre": "11",
    "dic": "12", "diciembre": "12",
}


# ================== HELPERS ================== #

def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def normalize_hhmm(h: str | None) -> str:
    if not h:
        return "00:00"

    s = str(h).strip().lower()
    s = s.replace(" ", "").replace("h", "")
    s = re.sub(r"[^0-9:]", "", s)
    s = s.rstrip(":")

    m = re.match(r"^(\d{1,2})(?::?(\d{2}))?$", s)
    if not m:
        return s

    return f"{int(m.group(1)):02d}:{int(m.group(2) or '00'):02d}"


def load_onebox_cache() -> dict:
    if not ONEBOX_CACHE_PATH.exists():
        return {}

    try:
        return json.loads(ONEBOX_CACHE_PATH.read_text("utf-8"))
    except Exception:
        return {}


def save_onebox_cache(cache: dict) -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    ONEBOX_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        "utf-8",
    )
    print("✔ Actualizado docs/onebox_cache.json")


# ================== OUTPUT ================== #

def write_html(payload: dict) -> None:
    if not TEMPLATE_PATH.exists():
        print("❌ Error: No existe template.html")
        return

    html_template = TEMPLATE_PATH.read_text("utf-8")
    html = html_template.replace(
        "{{PAYLOAD_JSON}}",
        json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    )

    DOCS_DIR.mkdir(exist_ok=True)

    (DOCS_DIR / "index.html").write_text(html, "utf-8")
    print("✔ Generado docs/index.html")

    if MANIFEST_PATH.exists():
        shutil.copy(MANIFEST_PATH, DOCS_DIR / "manifest.json")
        print("✔ Copiado manifest.json")

    if SW_PATH.exists():
        shutil.copy(SW_PATH, DOCS_DIR / "sw.js")
        print("✔ Copiado sw.js")


def write_schedule_json(payload: dict) -> None:
    DOCS_DIR.mkdir(exist_ok=True)

    (DOCS_DIR / "schedule.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        "utf-8",
    )

    print("✔ Generado docs/schedule.json")


# ================== DINATICKET ================== #

def parse_dinaticket_hour(raw: str) -> str | None:
    hora_txt = raw.strip().lower()
    hora_txt = hora_txt.replace(" ", "").replace("h", ":").rstrip(":")

    m = re.match(r"^(\d{1,2})(?::?(\d{2}))?$", hora_txt)
    if not m:
        return hora_txt or None

    hh = int(m.group(1))
    mm = int(m.group(2) or "00")
    return f"{hh:02d}:{mm:02d}"


def fetch_functions_dinaticket(url: str, timeout: int = 20) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    out: list[dict] = []

    for session in soup.find_all("div", class_="js-session-row"):
        parent = session.find_parent("div", class_="js-session-group")
        if not parent:
            continue

        date_div = parent.find("div", class_="session-card__date")
        if not date_div:
            continue

        dia = date_div.find("span", class_="num_dia")
        mes = date_div.find("span", class_="mes")
        if not dia or not mes:
            continue

        mes_txt = mes.get_text(strip=True)
        mes_num = MESES.get(mes_txt) or MESES.get(mes_txt.replace(".", ""))

        if not mes_num:
            print("DEBUG mes no reconocido Dinaticket:", repr(mes_txt))
            continue

        now = datetime.now(TZ)
        anio = now.year

        fecha_iso_tmp = f"{anio}-{mes_num}-{dia.get_text(strip=True).zfill(2)}"
        fecha_dt = datetime.strptime(fecha_iso_tmp, "%Y-%m-%d")

        if fecha_dt.date() < now.date():
            fecha_dt = fecha_dt.replace(year=anio + 1)

        fecha_iso = fecha_dt.strftime("%Y-%m-%d")
        fecha_label = fecha_dt.strftime("%d %b %Y")

        hora_span = session.find("span", class_="session-card__time-session")
        hora = parse_dinaticket_hour(hora_span.get_text(strip=True) if hora_span else "")

        quotas = session.find_all("div", class_="js-quota-row")

        if not quotas:
            cap = None
            stock = None
            vendidas = None
        else:
            cap = sum(safe_int(q.get("data-quota-total", 0)) for q in quotas)
            stock = sum(safe_int(q.get("data-stock", 0)) for q in quotas)
            vendidas = max(0, cap - stock)

        out.append({
            "fecha_label": fecha_label,
            "fecha_iso": fecha_iso,
            "hora": hora,
            "vendidas_dt": vendidas,
            "capacidad": cap,
            "stock": stock,
        })

    return sorted(out, key=lambda f: (f["fecha_iso"], f.get("hora") or "00:00"))


# ================== ONEBOX / ESCALERA ================== #

def parse_onebox_date(raw: str) -> tuple[str, str] | None:
    raw = raw.replace("\xa0", " ")
    raw = " ".join(raw.split()).lower()

    m = re.search(
        r"(?:lun|mar|mi[eé]|jue|vie|s[aá]b|dom)\.?,?\s+"
        r"(\d{1,2})\s+([a-záéíóúñ]+)\s+(\d{4})\s*-\s*(\d{1,2}):(\d{2})",
        raw,
        re.IGNORECASE,
    )

    if not m:
        return None

    dia, mes_txt, anio, hh, mm = m.groups()
    mes_key = mes_txt.lower().replace(".", "")
    mes_num = MESES_ES.get(mes_key)

    if not mes_num:
        print("DEBUG mes Onebox no reconocido:", repr(mes_txt))
        return None

    fecha_iso = f"{anio}-{mes_num}-{dia.zfill(2)}"
    hora = f"{int(hh):02d}:{mm}"

    return fecha_iso, hora


def extract_onebox_dates_from_text(text: str) -> list[str]:
    text = text.replace("\xa0", " ")
    text = " ".join(text.split())

    pattern = re.compile(
        r"(?:lun|mar|mi[eé]|jue|vie|s[aá]b|dom)\.?,?\s+"
        r"\d{1,2}\s+"
        r"(?:ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic|"
        r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|octubre|noviembre|diciembre)"
        r"\s+\d{4}\s*-\s*\d{1,2}:\d{2}",
        re.IGNORECASE,
    )

    return pattern.findall(text)


def count_onebox_stock_playwright(page) -> tuple[int | None, int | None]:
    available_selectors = [
        ".seat.available",
        ".available",
        ".is-available",
        "[data-status='available']",
        "[data-state='available']",
        "[data-seat-status='available']",
        "[data-availability='available']",
        "button:not([disabled])[aria-label*='Asiento']",
        "button:not([disabled])[aria-label*='Butaca']",
        "button:not([disabled])[aria-label*='Seat']",
        "svg [role='button']:not([aria-disabled='true'])",
    ]

    total_selectors = [
        ".seat",
        "[data-seat-id]",
        "[data-place-id]",
        "[data-seat]",
        "button[aria-label*='Asiento']",
        "button[aria-label*='Butaca']",
        "button[aria-label*='Seat']",
        "svg [role='button']",
    ]

    stock = None
    capacidad = None

    for selector in available_selectors:
        try:
            n = page.locator(selector).count()
            if n:
                stock = n
                break
        except Exception:
            pass

    for selector in total_selectors:
        try:
            n = page.locator(selector).count()
            if n:
                capacidad = n
                break
        except Exception:
            pass

    return stock, capacidad


def get_onebox_select_urls(page, parent_url: str) -> list[dict]:
    fallback = ONEBOX_FALLBACK_SELECTS.get(parent_url, [])
    fallback_by_url = {
        item["url"]: item
        for item in fallback
        if isinstance(item, dict) and item.get("url")
    }

    if "/select/" in parent_url:
        return [{"url": parent_url}]

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    select_urls: list[str] = []

    for delay in [3000, 6000, 9000]:
        page.wait_for_timeout(delay)

        try:
            hrefs = page.eval_on_selector_all(
                "a[href]",
                """els => els.map(a => a.href).filter(h => h.includes('/select/'))"""
            )
        except Exception:
            hrefs = []

        select_urls = sorted(set(select_urls + hrefs))

        if select_urls:
            break

    if not select_urls and fallback:
        print(f"⚠️ Onebox sin enlaces dinámicos; usando fallback: {len(fallback)} URLs")
        return fallback

    return [
        fallback_by_url.get(url, {"url": url})
        for url in select_urls
    ]


def fetch_functions_onebox(url: str) -> list[dict]:
    if sync_playwright is None:
        print("DEBUG Playwright no está instalado")
        return []

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    cache = load_onebox_cache()
    cache_changed = False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        page = browser.new_page(
            user_agent=UA["User-Agent"],
            viewport={"width": 1440, "height": 1100},
            locale="es-ES",
            timezone_id="Europe/Madrid",
        )

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"ERROR Onebox página padre: {e}")
            browser.close()
            return []

        select_items = get_onebox_select_urls(page, url)
        print("DEBUG Onebox select URLs:", select_items)

        for select_item in select_items:
            select_url = select_item["url"]

            try:
                page.goto(select_url, wait_until="domcontentloaded", timeout=45000)

                try:
                    page.wait_for_selector(".seat, .available", timeout=15000)
                except Exception:
                    page.wait_for_timeout(5000)

                body_text = page.locator("body").inner_text(timeout=15000)
                current_date_texts = extract_onebox_dates_from_text(body_text)

                if current_date_texts:
                    parsed = parse_onebox_date(current_date_texts[0])
                    if not parsed:
                        print(f"DEBUG Onebox fecha no parseable: {current_date_texts[0]}")
                        continue

                    fecha_iso, hora = parsed
                else:
                    fecha_iso = select_item.get("fecha_iso")
                    hora = select_item.get("hora")

                    if not fecha_iso or not hora:
                        print(f"DEBUG Onebox sin fecha visible y sin fallback: {select_url}")
                        continue

                key = (fecha_iso, hora)

                if key in seen:
                    continue

                seen.add(key)

                stock, capacidad = count_onebox_stock_playwright(page)
                cache_key = f"{fecha_iso}|{hora}|{select_url}"

                if stock is not None and capacidad is not None:
                    vendidas = max(0, capacidad - stock)
                    cache[cache_key] = {
                        "stock": stock,
                        "capacidad": capacidad,
                        "vendidas_dt": vendidas,
                        "updated_at": datetime.now(TZ).isoformat(),
                    }
                    cache_changed = True
                else:
                    old = cache.get(cache_key)

                    if old:
                        stock = old.get("stock")
                        capacidad = old.get("capacidad")
                        vendidas = old.get("vendidas_dt")
                        print(
                            f"↩ Usando cache Onebox para {fecha_iso} {hora}: "
                            f"stock={stock}, cap={capacidad}"
                        )
                    else:
                        vendidas = None
                        print(f"⚠️ Sin stock Onebox ni cache para {fecha_iso} {hora}")

                fecha_dt = datetime.strptime(fecha_iso, "%Y-%m-%d")
                fecha_label = fecha_dt.strftime("%d %b %Y")

                out.append({
                    "fecha_label": fecha_label,
                    "fecha_iso": fecha_iso,
                    "hora": hora,
                    "vendidas_dt": vendidas,
                    "capacidad": capacidad,
                    "stock": stock,
                })

            except Exception as e:
                print(f"ERROR Onebox select {select_url}: {e}")

        browser.close()

    if cache_changed:
        save_onebox_cache(cache)

    return sorted(out, key=lambda f: (f["fecha_iso"], f.get("hora") or "00:00"))


# ================== ABONOTEATRO ================== #

def fetch_abonoteatro_shows(url: str, timeout: int = 20) -> set[tuple[str, str]]:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    out: set[tuple[str, str]] = set()

    sesiones = soup.find_all("div", class_="bsesion")

    for ses in sesiones:
        if not ses.find("a", class_="buyBtn"):
            continue

        fecha_div = ses.find("div", class_="bfechasesion")
        if not fecha_div:
            continue

        mes_y_anio_tag = fecha_div.find("p", class_="psess")
        dia_tag = fecha_div.find("p", class_="psesb")
        hora_h3 = ses.find("h3", class_="horasesion")

        if not mes_y_anio_tag or not dia_tag or not hora_h3:
            continue

        raw = mes_y_anio_tag.get_text(strip=True).lower()
        m_ma = re.match(r"^([a-záéíóúñ]+)\s+(\d{4})$", raw)

        if not m_ma:
            print("DEBUG mes/año raro en AbonoTeatro:", repr(raw))
            continue

        mes_nombre = m_ma.group(1)
        anio = m_ma.group(2)
        mes_num = MESES_LARGO.get(mes_nombre)

        if not mes_num:
            print("DEBUG mes desconocido AbonoTeatro:", mes_nombre)
            continue

        dia_num = re.sub(r"\D", "", dia_tag.get_text(strip=True)).zfill(2)

        hora_txt = hora_h3.get_text(" ", strip=True)
        m_hora = re.search(r"(\d{1,2}):(\d{2})", hora_txt)

        if not m_hora:
            print("DEBUG hora rara AbonoTeatro:", repr(hora_txt))
            continue

        hora = f"{m_hora.group(1).zfill(2)}:{m_hora.group(2).zfill(2)}"
        fecha_iso = f"{anio}-{mes_num}-{dia_num}"

        out.add((fecha_iso, hora))

    print("DEBUG AbonoTeatro fechas/hora:", sorted(out))
    return out


# ================== FEVER ================== #

def fetch_fever_dates(url: str, timeout: int = 15) -> set[str]:
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()

        m = re.search(r'"datesWithSessions"\s*:\s*\[(.*?)\]', r.text)
        if not m:
            return set()

        raw = m.group(1)
        fechas = re.findall(r'"(\d{4}-\d{2}-\d{2})"', raw)
        return set(fechas)

    except Exception as e:
        print(f"ERROR Fever scraping {url}: {e}")
        return set()


# ================== PAYLOAD ================== #

def build_rows(funcs: list[dict]) -> list[list]:
    return [
        [
            f.get("fecha_label"),
            f.get("hora"),
            f.get("vendidas_dt"),
            f.get("fecha_iso"),
            f.get("capacidad"),
            f.get("stock"),
            f.get("abono_estado"),
            f.get("fever_estado"),
        ]
        for f in funcs
    ]


def build_payload(eventos: dict[str, list[dict]], abono_shows: set[tuple[str, str]]) -> dict:
    now = datetime.now(TZ)
    out: dict[str, dict] = {}

    abono_fechas = {fecha for fecha, _hora in abono_shows}

    for sala, funcs in eventos.items():
        if sala == "Escondido":
            for f in funcs:
                fecha = f["fecha_iso"]
                hora = f["hora"]

                if (fecha, hora) in abono_shows:
                    f["abono_estado"] = "venta"
                elif fecha in abono_fechas:
                    f["abono_estado"] = "venta"
                else:
                    f["abono_estado"] = "agotado"
        else:
            for f in funcs:
                f["abono_estado"] = None

        if sala in FEVER_URLS:
            fever_dates = fetch_fever_dates(FEVER_URLS[sala])
            print(f"DEBUG Fever {sala} fechas:", sorted(fever_dates))

            for f in funcs:
                f["fever_estado"] = "venta" if f["fecha_iso"] in fever_dates else "agotado"
        else:
            for f in funcs:
                f["fever_estado"] = None

        proximas: list[dict] = []
        pasadas: list[dict] = []

        for f in funcs:
            fecha_iso = f["fecha_iso"]
            hora_txt = f.get("hora") or "00:00"

            try:
                ses_dt = datetime.strptime(
                    f"{fecha_iso} {hora_txt}",
                    "%Y-%m-%d %H:%M"
                ).replace(tzinfo=TZ)
            except Exception:
                ses_dt = None

            if ses_dt and ses_dt >= now:
                proximas.append(f)
            elif ses_dt:
                pasadas.append(f)
            else:
                d = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
                if d >= now.date():
                    proximas.append(f)
                else:
                    pasadas.append(f)

        proximas.sort(key=lambda f: (f["fecha_iso"], f.get("hora") or "00:00"))

        print(
            f"[DEBUG] {sala}: total={len(funcs)} "
            f"· proximas={len(proximas)} · pasadas={len(pasadas)}"
        )

        out[sala] = {
            "table": {
                "headers": [
                    "Fecha", "Hora", "Vendidas", "FechaISO",
                    "Capacidad", "Stock", "Abono", "Fever"
                ],
                "rows": build_rows(proximas),
            },
            "proximas": {
                "table": {
                    "headers": [
                        "Fecha", "Hora", "Vendidas", "FechaISO",
                        "Capacidad", "Stock", "Abono", "Fever"
                    ],
                    "rows": build_rows(proximas),
                }
            },
        }

    return {
        "generated_at": datetime.now(TZ).isoformat(),
        "eventos": out,
        "fever_urls": FEVER_URLS,
    }


# ================== MAIN ================== #

if __name__ == "__main__":
    current: dict[str, list[dict]] = {}

    for sala, url in DINATICKET_EVENTS.items():
        try:
            funcs = fetch_functions_dinaticket(url)
        except Exception as e:
            print(f"ERROR Dinaticket {sala}: {e}")
            funcs = []

        current[sala] = funcs
        print(f"{sala}: {len(funcs)} funciones Dinaticket extraídas")

    for sala, url in ONEBOX_EVENTS.items():
        try:
            funcs = fetch_functions_onebox(url)
        except Exception as e:
            print(f"ERROR Onebox {sala}: {e}")
            funcs = []

        current[sala] = funcs
        print(f"{sala}: {len(funcs)} funciones Onebox extraídas")
        print("DEBUG Onebox funcs:", funcs)

    try:
        abono_shows = fetch_abonoteatro_shows(ABONO_URL)
        print(f"AbonoTeatro: {len(abono_shows)} funciones en venta")
    except Exception as e:
        print(f"Error al leer AbonoTeatro: {e}")
        abono_shows = set()

    payload = build_payload(current, abono_shows)

    write_html(payload)
    write_schedule_json(payload)
