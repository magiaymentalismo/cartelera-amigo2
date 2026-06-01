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

# ===================== CONFIG ===================== #

DINATICKET_EVENTS = {
    "Escondido": "https://www.dinaticket.com/es/provider/20073/event/4919204",
    "Oniria": "https://www.dinaticket.com/es/provider/20073/event/4940326",
}

ONEBOX_EVENTS = {
    "Escalera": "https://entradas.laescaleradejacob.es/laescaleradejacob/select/2877829",
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


# ================== HTML / JSON OUTPUT ================== #

def write_html(payload: dict) -> None:
    if not TEMPLATE_PATH.exists():
        print("❌ Error: No existe template.html")
        return

    html_template = TEMPLATE_PATH.read_text("utf-8")
    html = html_template.replace(
        "{{PAYLOAD_JSON}}",
        json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    )

    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    (docs_dir / "index.html").write_text(html, "utf-8")
    print("✔ Generado docs/index.html")

    if MANIFEST_PATH.exists():
        shutil.copy(MANIFEST_PATH, docs_dir / "manifest.json")
        print("✔ Copiado manifest.json")

    if SW_PATH.exists():
        shutil.copy(SW_PATH, docs_dir / "sw.js")
        print("✔ Copiado sw.js")


def write_schedule_json(payload: dict) -> None:
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    (docs_dir / "schedule.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        "utf-8",
    )
    print("✔ Generado docs/schedule.json")


# ================== SCRAPER DINATICKET ================== #

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
        if not (dia and mes):
            continue

        mes_txt = mes.text.strip()
        mes_num = MESES.get(mes_txt) or MESES.get(mes_txt.replace(".", ""))
        if not mes_num:
            print("DEBUG mes no reconocido Dinaticket:", repr(mes_txt))
            continue

        now = datetime.now(TZ)
        anio = now.year

        fecha_iso_tmp = f"{anio}-{mes_num}-{dia.text.strip().zfill(2)}"
        fecha_dt = datetime.strptime(fecha_iso_tmp, "%Y-%m-%d")

        if fecha_dt.date() < now.date():
            fecha_dt = fecha_dt.replace(year=anio + 1)

        fecha_iso = fecha_dt.strftime("%Y-%m-%d")
        fecha_label = fecha_dt.strftime("%d %b %Y")

        hora_span = session.find("span", class_="session-card__time-session")
        hora_txt = (hora_span.text if hora_span else "").strip().lower()
        hora_txt = hora_txt.replace(" ", "").replace("h", ":")

        m = re.match(r"^(\d{1,2})(?::?(\d{2}))?$", hora_txt)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or "00")
            hora = f"{hh:02d}:{mm:02d}"
        else:
            hora = hora_txt

        quota = session.find("div", class_="js-quota-row")
        cap = int(quota.get("data-quota-total", 0)) if quota else None
        stock = int(quota.get("data-stock", 0)) if quota else None
        vendidas = max(0, cap - stock) if cap is not None and stock is not None else None

        out.append({
            "fecha_label": fecha_label,
            "fecha_iso": fecha_iso,
            "hora": hora,
            "vendidas_dt": vendidas,
            "capacidad": cap,
            "stock": stock,
        })

    return out


# ================== SCRAPER ONEBOX / ESCALERA ================== #

def fetch_functions_onebox(url: str, timeout: int = 20) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    pattern = re.compile(
        r"\b(?:lun|mar|mi[eé]|jue|vie|s[aá]b|dom)\.?,?\s+"
        r"(\d{1,2})\s+([a-záéíóúñ]+)\s+(\d{4})\s*-\s*(\d{1,2}):(\d{2})",
        re.IGNORECASE,
    )

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for m in pattern.finditer(text):
        dia, mes_txt, anio, hh, mm = m.groups()
        mes_key = mes_txt.lower().replace(".", "")
        mes_num = MESES_ES.get(mes_key)

        if not mes_num:
            print("DEBUG mes no reconocido Onebox:", repr(mes_txt))
            continue

        fecha_iso = f"{anio}-{mes_num}-{dia.zfill(2)}"
        hora = f"{int(hh):02d}:{mm}"

        key = (fecha_iso, hora)
        if key in seen:
            continue
        seen.add(key)

        fecha_dt = datetime.strptime(fecha_iso, "%Y-%m-%d")
        fecha_label = fecha_dt.strftime("%d %b %Y")

        out.append({
            "fecha_label": fecha_label,
            "fecha_iso": fecha_iso,
            "hora": hora,
            "vendidas_dt": None,
            "capacidad": None,
            "stock": None,
        })

    return out


# ================== SCRAPER ABONOTEATRO ================== #

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
        if not mes_y_anio_tag:
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
            print("DEBUG mes desconocido:", mes_nombre)
            continue

        dia_tag = fecha_div.find("p", class_="psesb")
        if not dia_tag:
            continue

        dia_num = re.sub(r"\D", "", dia_tag.get_text(strip=True)).zfill(2)

        hora_h3 = ses.find("h3", class_="horasesion")
        if not hora_h3:
            continue

        hora_txt = hora_h3.get_text(" ", strip=True)
        m_hora = re.search(r"(\d{1,2}):(\d{2})", hora_txt)
        if not m_hora:
            print("DEBUG hora rara:", repr(hora_txt))
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
            f["fecha_label"],
            f["hora"],
            f["vendidas_dt"],
            f["fecha_iso"],
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

        if sala in ["Miedo", "Disfruta"]:
            fever_url = FEVER_URLS.get(sala)
            fever_dates = fetch_fever_dates(fever_url) if fever_url else set()

            print(f"DEBUG Fever {sala} fechas:", sorted(fever_dates))

            for f in funcs:
                fecha = f["fecha_iso"]
                f["fever_estado"] = "venta" if fecha in fever_dates else "agotado"
        else:
            for f in funcs:
                f["fever_estado"] = None

        proximas: list[dict] = []
        pasadas: list[dict] = []

        for f in funcs:
            fecha_iso = f["fecha_iso"]
            hora_txt = f["hora"] or "00:00"

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

        proximas.sort(key=lambda f: (f["fecha_iso"], f["hora"] or "00:00"))

        print(
            f"[DEBUG] {sala}: total={len(funcs)} "
            f"· proximas={len(proximas)} · pasadas={len(pasadas)}"
        )

        if pasadas:
            print(f"[INFO] Eliminando {len(pasadas)} funciones pasadas de {sala}")

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
        funcs = fetch_functions_dinaticket(url)
        current[sala] = funcs
        print(f"{sala}: {len(funcs)} funciones Dinaticket extraídas")

    for sala, url in ONEBOX_EVENTS.items():
        funcs = fetch_functions_onebox(url)
        current[sala] = funcs
        print(f"{sala}: {len(funcs)} funciones Onebox extraídas")

    try:
        abono_shows = fetch_abonoteatro_shows(ABONO_URL)
        print(f"AbonoTeatro: {len(abono_shows)} funciones en venta")
    except Exception as e:
        print(f"Error al leer AbonoTeatro: {e}")
        abono_shows = set()

    payload = build_payload(current, abono_shows)

    write_html(payload)
    write_schedule_json(payload)
