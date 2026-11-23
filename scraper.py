import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "GachaEventTrackerBot/1.0 (Android personal tracker; MediaWiki API)"
}

GAME_COLORS = {
    "hsr": "#5C6BC0",
    "zzz": "#FF7043",
}

DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%B %d, %Y %I:%M:%S %p",
    "%B %d, %Y %H:%M:%S",
    "%B %d, %Y %H:%M",
    "%B %d, %Y",
    "%d %B, %Y %H:%M:%S",
    "%d %B, %Y %H:%M",
    "%d %B, %Y",
    "%d %B %Y %H:%M:%S",
    "%d %B %Y %H:%M",
    "%d %B %Y",
    "%b %d, %Y %H:%M",
    "%b %d, %Y",
]


def slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return clean[:60]


def classify_event_type(title: str, default_type: str = "EVENTO") -> str:
    t = title.lower()
    endgame_keywords = [
        "memory of chaos", "pure fiction", "apocalyptic shadow",
        "shiyu defense", "deadly assault", "threshold simulation",
        "anomaly arbitration", "annihilation simulacrum"
    ]
    banner_keywords = [
        "warp", "channel", "signal search", "brilliant fixation",
        "coalesced truths", "indelible coterie", "comedy of stars",
        "w-engine", "exclusive channel"
    ]
    if any(k in t for k in endgame_keywords):
        return "ENDGAME"
    if any(k in t for k in banner_keywords):
        return "BANNER"
    return default_type


def parse_offset_str(offset_raw: str) -> Optional[str]:
    if not offset_raw:
        return None
    s = offset_raw.strip().upper()
    if "SERVER" in s:
        return None
    if s in ("Z", "UTC", "GMT"):
        return "+00:00"
    m = re.search(r"(?:GMT|UTC)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", s)
    if m:
        sign = m.group(1)
        hh = int(m.group(2))
        mm = int(m.group(3) or 0)
        return f"{sign}{hh:02d}:{mm:02d}"
    return None


def parse_datetime_value(
    raw: str,
    default_time: str = "04:00:00",
    explicit_offset: Optional[str] = None
) -> Optional[tuple[str, str]]:
    """
    Devuelve (iso_string, time_mode) donde time_mode es 'GLOBAL' o 'SERVER_TIME'.
    """
    if not raw:
        return None
    s = raw.strip()
    if any(skip in s.lower() for skip in ("indefinite", "tba", "unknown", "permanent", "to be announced")):
        return None

    # Extraemos posible zona horaria embebida en el texto
    detected_offset = explicit_offset
    if re.search(r"\bUTC\b", s, re.IGNORECASE) and not re.search(r"UTC[+-]", s, re.IGNORECASE):
        detected_offset = "+00:00"
    else:
        tz_match = re.search(r"(?:GMT|UTC)\s*([+-]\s*\d{1,2}(?::?\d{2})?)", s, re.IGNORECASE)
        if tz_match:
            detected_offset = parse_offset_str(tz_match.group(0))

    is_server_time = "server" in s.lower()

    # Limpiamos anotaciones entre paréntesis o textos extra
    cleaned = re.sub(r"\(.*?\)", "", s)
    cleaned = re.sub(r"\b(?:GMT|UTC)\s*(?:[+-]\s*\d{1,2}(?::?\d{2})?)?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-–—")

    # Si tiene versión al inicio ej "After the Version 4.7 update on 2026/11/11"
    m_after = re.search(r"(\d{4}[/-]\d{1,2}[/-]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", cleaned)
    if m_after and len(cleaned) > len(m_after.group(1)) + 8:
        cleaned = m_after.group(1)

    parsed_dt = None
    has_explicit_time = bool(re.search(r"\d{1,2}:\d{2}", cleaned))

    for fmt in DATE_FORMATS:
        try:
            parsed_dt = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue

    if parsed_dt is None:
        return None

    if not has_explicit_time:
        hh, mm, ss = map(int, default_time.split(":"))
        parsed_dt = parsed_dt.replace(hour=hh, minute=mm, second=ss)

    iso_base = parsed_dt.strftime("%Y-%m-%dT%H:%M:%S")

    if detected_offset and not is_server_time:
        return (f"{iso_base}{detected_offset}", "GLOBAL")
    return (iso_base, "SERVER_TIME")


def fetch_parse_html(api_url: str, page: str) -> Optional[BeautifulSoup]:
    params = {
        "action": "parse",
        "page": page,
        "prop": "text",
        "format": "json",
        "redirects": "1"
    }
    resp = requests.get(api_url, params=params, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    data = resp.json()
    html = data.get("parse", {}).get("text", {}).get("*")
    if not html:
        return None
    return BeautifulSoup(html, "html.parser")


def fetch_category_titles(api_url: str, category: str, limit: int = 40) -> list[str]:
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmnamespace": "0",
        "cmlimit": str(limit),
        "format": "json"
    }
    resp = requests.get(api_url, params=params, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    members = resp.json().get("query", {}).get("categorymembers", [])
    return [m["title"] for m in members]


def fetch_wikitext_batch(api_url: str, titles: list[str]) -> dict[str, str]:
    result = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        params = {
            "action": "query",
            "titles": "|".join(chunk),
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
            "redirects": "1"
        }
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        for page_data in pages.values():
            title = page_data.get("title")
            revs = page_data.get("revisions", [])
            if title and revs:
                content = revs[0].get("slots", {}).get("main", {}).get("*", "")
                if content:
                    result[title] = content
    return result


def extract_template_params(wikitext: str) -> dict[str, str]:
    """Extrae parámetros simples tipo |key = value de infoboxes de Fandom."""
    params = {}
    for line in wikitext.splitlines():
        m = re.match(r"^\s*\|\s*([a-zA-Z0-9_ ]+?)\s*=\s*(.+?)\s*$", line)
        if m:
            key = m.group(1).strip().lower().replace(" ", "_")
            val = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", m.group(2).strip())
            val = re.sub(r"\{\{.*?\}\}", "", val).strip()
            if val:
                params[key] = val
    return params


def is_relevant_window(end_iso: str) -> bool:
    """Conserva eventos que no hayan terminado hace más de 10 días."""
    try:
        clean = end_iso[:19]
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=10)
        return dt >= cutoff
    except Exception:
        return True


def parse_infobox_event(
    game_id: str,
    title: str,
    wikitext: str,
    default_type: str
) -> Optional[dict]:
    p = extract_template_params(wikitext)

    start_raw = p.get("time_start") or p.get("start") or p.get("date_start")
    end_raw = p.get("time_end") or p.get("end") or p.get("date_end")

    # Si no está en parámetros de infobox, intentamos leer la sección == Duration ==
    if not (start_raw and end_raw):
        dur_match = re.search(r"={2,3}\s*Duration\s*={2,3}\s*(.+)", wikitext, re.IGNORECASE)
        if dur_match:
            line = dur_match.group(1).strip()
            parts = re.split(r"\s*[–—-]\s+", line, maxsplit=1)
            if len(parts) == 2:
                start_raw, end_raw = parts[0], parts[1]

    if not (start_raw and end_raw):
        return None

    start_offset = parse_offset_str(p.get("time_start_offset", ""))
    end_offset = parse_offset_str(p.get("time_end_offset", ""))

    parsed_start = parse_datetime_value(start_raw, default_time="11:00:00", explicit_offset=start_offset)
    parsed_end = parse_datetime_value(end_raw, default_time="03:59:00", explicit_offset=end_offset)

    if not parsed_start or not parsed_end:
        return None
    if not is_relevant_window(parsed_end[0]):
        return None

    clean_title = re.sub(r"/\d{4}-\d{2}-\d{2}$", "", title).strip()
    event_type = classify_event_type(f"{clean_title} {p.get('type', '')}", default_type)

    return {
        "id": f"{game_id}-{slugify(title)}",
        "gameId": game_id,
        "title": clean_title,
        "type": event_type,
        "startRaw": parsed_start[0],
        "startMode": parsed_start[1],
        "endRaw": parsed_end[0],
        "endMode": parsed_end[1],
        "colorHex": GAME_COLORS[game_id],
        "imageUrl": None
    }


# ==========================================
# SCRAPER 1: HONKAI STAR RAIL
# ==========================================

def scrape_hsr() -> list[dict]:
    api_url = "https://honkai-star-rail.fandom.com/api.php"
    events_by_id: dict[str, dict] = {}

    # 1) Eventos actuales y próximos desde las categorías oficiales de la wiki
    titles_to_fetch = set()
    for cat in ("Category:Current_Events", "Category:Upcoming_Content"):
        try:
            titles_to_fetch.update(fetch_category_titles(api_url, cat, limit=40))
        except Exception as e:
            print(f"[HSR] Error leyendo {cat}: {e}")

    # 2) Warps (Banners) actuales y próximos desde Warp/List
    try:
        warp_soup = fetch_parse_html(api_url, "Warp/List")
        if warp_soup:
            for a in warp_soup.select("a[href^='/wiki/']"):
                href = a.get("href", "")
                if re.search(r"/202\d-\d{2}-\d{2}$", href):
                    page_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                    titles_to_fetch.add(page_title)
    except Exception as e:
        print(f"[HSR] Error leyendo Warp/List: {e}")

    # 3) Descargamos wikitext en lote y parseamos sus fechas exactas
    if titles_to_fetch:
        wikitexts = fetch_wikitext_batch(api_url, list(titles_to_fetch))
        for title, wt in wikitexts.items():
            is_warp = bool(re.search(r"/202\d-\d{2}-\d{2}$", title)) and "warp" in wt.lower()
            default_type = "BANNER" if is_warp else "EVENTO"
            parsed = parse_infobox_event("hsr", title, wt, default_type)
            if parsed:
                events_by_id[parsed["id"]] = parsed

    print(f"[HSR] Eventos capturados: {len(events_by_id)}")
    return list(events_by_id.values())


# ==========================================
# SCRAPER 2: ZENLESS ZONE ZERO
# ==========================================

def scrape_zzz() -> list[dict]:
    api_url = "https://zenless-zone-zero.fandom.com/api.php"
    events_by_id: dict[str, dict] = {}
    linked_titles = set()

    # 1) Tabla de eventos actuales y próximos en la página Event
    try:
        soup = fetch_parse_html(api_url, "Event")
        if soup:
            for table in soup.select("table"):
                # Ignoramos la tabla de historial si existiera
                rows = table.select("tr")
                for row in rows[1:]:
                    cols = row.find_all(["td", "th"])
                    if len(cols) < 2:
                        continue
                    title_text = cols[0].get_text(" ", strip=True)
                    duration_text = cols[1].get_text(" ", strip=True)
                    type_text = cols[2].get_text(" ", strip=True) if len(cols) > 2 else ""

                    # Guardamos enlaces internos por si tienen hora más precisa
                    link = cols[0].find("a", href=re.compile(r"^/wiki/"))
                    if link:
                        t = requests.utils.unquote(link["href"].replace("/wiki/", "")).replace("_", " ")
                        linked_titles.add((t, "EVENTO"))

                    parts = re.split(r"\s*[–—-]\s*", duration_text, maxsplit=1)
                    if len(parts) == 2 and title_text:
                        s_val = parse_datetime_value(parts[0], default_time="10:00:00")
                        e_val = parse_datetime_value(parts[1], default_time="03:59:00")
                        if s_val and e_val and is_relevant_window(e_val[0]):
                            ev_type = classify_event_type(f"{title_text} {type_text}", "EVENTO")
                            ev_id = f"zzz-{slugify(title_text)}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": "zzz",
                                "title": title_text,
                                "type": ev_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS["zzz"],
                                "imageUrl": None
                            }
    except Exception as e:
        print(f"[ZZZ] Error leyendo Event: {e}")

    # 2) Banners de Exclusive Channel y W-Engine Channel
    for channel_page in ("Exclusive_Channel", "W-Engine_Channel"):
        try:
            csoup = fetch_parse_html(api_url, channel_page)
            if csoup:
                for a in csoup.select("a[href^='/wiki/']"):
                    href = a.get("href", "")
                    if re.search(r"/202\d-\d{2}-\d{2}$", href):
                        t = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                        linked_titles.add((t, "BANNER"))
        except Exception as e:
            print(f"[ZZZ] Error leyendo {channel_page}: {e}")

    # 3) Enriquecemos con el wikitext de las páginas enlazadas (tienen horas exactas)
    if linked_titles:
        title_list = [t for t, _ in linked_titles]
        type_hint = {t: tp for t, tp in linked_titles}
        wikitexts = fetch_wikitext_batch(api_url, title_list)
        for title, wt in wikitexts.items():
            parsed = parse_infobox_event("zzz", title, wt, type_hint.get(title, "EVENTO"))
            if parsed:
                # Si ya estaba en la tabla por nombre corto, reemplazamos con la versión con hora exacta
                short_id = f"zzz-{slugify(parsed['title'])}"
                if short_id in events_by_id:
                    del events_by_id[short_id]
                events_by_id[parsed["id"]] = parsed

    print(f"[ZZZ] Eventos capturados: {len(events_by_id)}")
    return list(events_by_id.values())


# ==========================================
# ENSAMBLADO FINAL CON FALLBACK Y OVERRIDES
# ==========================================

def load_json_safe(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Aviso leyendo {path}: {e}")
    return default


def main():
    previous_data = load_json_safe("events.json", {"events": []})
    previous_events = previous_data.get("events", [])

    final_events: list[dict] = []

    # HSR
    try:
        hsr_events = scrape_hsr()
        if hsr_events:
            final_events.extend(hsr_events)
        else:
            print("[HSR] Sin resultados nuevos, conservando datos previos.")
            final_events.extend([e for e in previous_events if e.get("gameId") == "hsr"])
    except Exception as e:
        print(f"[HSR] Fallo general: {e}")
        final_events.extend([e for e in previous_events if e.get("gameId") == "hsr"])

    # ZZZ
    try:
        zzz_events = scrape_zzz()
        if zzz_events:
            final_events.extend(zzz_events)
        else:
            print("[ZZZ] Sin resultados nuevos, conservando datos previos.")
            final_events.extend([e for e in previous_events if e.get("gameId") == "zzz"])
    except Exception as e:
        print(f"[ZZZ] Fallo general: {e}")
        final_events.extend([e for e in previous_events if e.get("gameId") == "zzz"])

    # Aplicamos manual_overrides.json (si existe)
    overrides = load_json_safe("manual_overrides.json", [])
    by_id = {ev["id"]: ev for ev in final_events}
    for ov in overrides:
        if "id" in ov:
            by_id[ov["id"]] = ov

    sorted_events = sorted(by_id.values(), key=lambda x: (x["gameId"], x["startRaw"]))

    output = {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": sorted_events
    }

    with open("events.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Total final en events.json: {len(sorted_events)} eventos.")
    for ev in sorted_events:
        print(f" - [{ev['gameId'].upper()}][{ev['type']}] {ev['title']} ({ev['startRaw']} -> {ev['endRaw']})")


if __name__ == "__main__":
    main()
