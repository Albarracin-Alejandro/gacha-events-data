import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "GachaEventTrackerBot/1.5 (Android personal tracker; MediaWiki API)"
}

GAME_COLORS = {
    "hsr": "#5C6BC0",
    "zzz": "#FF7043",
    "wuwa": "#26A69A",
    "gi": "#AB47BC",
}

IGNORED_KEYWORDS = ("social media", "fan art", "fanart", "community event")

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


def clean_display_title(raw_title: str) -> str:
    t = raw_title.strip()
    m_file = re.match(r"^File:(?:Event\s+)?(.+?)\.(?:png|jpe?g|webp|gif)$", t, flags=re.IGNORECASE)
    if m_file:
        t = m_file.group(1).replace("_", " ").strip()
    else:
        t = re.sub(r"File:.*?\.(?:png|jpe?g|webp|gif)\s*", "", t, flags=re.IGNORECASE).strip()

    t = re.sub(r"[/\s]+\d{4}-\d{2}-\d{2}$", "", t).strip()

    if "/" in t:
        parts = [p.strip() for p in t.split("/") if p.strip()]
        if len(parts) >= 2:
            t = f"{parts[0]}: {parts[-1]}"

    return re.sub(r"\s+", " ", t).strip()


def should_ignore(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in IGNORED_KEYWORDS)


def clean_wikitext_value(raw_val: str) -> str:
    val = re.sub(r"<!--.*?-->", "", raw_val)
    val = re.sub(r"<[^>]+>", " ", val)
    val = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", val)
    val = re.sub(r"\{\{[^}]*\}\}", "", val)
    return re.sub(r"\s+", " ", val).strip()


def detect_types(
    title: str,
    raw_type_hint: str = "",
    wikitext: str = "",
    default_type: str = "EVENTO",
    default_subtype: str = "In-Game Event"
) -> tuple[str, str]:
    combined = f"{title} {raw_type_hint}".lower()
    wt_head = clean_wikitext_value(wikitext[:850]).lower()

    # 1. Endgame específico por juego
    if "memory of chaos" in combined or "forgotten hall" in combined:
        return ("ENDGAME", "Memory of Chaos")
    if "pure fiction" in combined:
        return ("ENDGAME", "Pure Fiction")
    if "apocalyptic shadow" in combined:
        return ("ENDGAME", "Apocalyptic Shadow")
    if "anomaly arbitration" in combined:
        return ("ENDGAME", "Anomaly Arbitration")

    if "shiyu defense" in combined:
        return ("ENDGAME", "Shiyu Defense")
    if "deadly assault" in combined:
        return ("ENDGAME", "Deadly Assault")
    if "threshold simulation" in combined or "myriad endgame" in combined:
        return ("ENDGAME", "Threshold Simulation")
    if "annihilation simulacrum" in combined:
        return ("ENDGAME", "Annihilation Simulacrum")

    if "tower of adversity" in combined:
        return ("ENDGAME", "Tower of Adversity")
    if "whimpering wastes" in combined:
        return ("ENDGAME", "Whimpering Wastes")

    if "spiral abyss" in combined:
        return ("ENDGAME", "Spiral Abyss")
    if "imaginarium theater" in combined:
        return ("ENDGAME", "Imaginarium Theater")
    if "stygian onslaught" in combined:
        return ("ENDGAME", "Stygian Onslaught")

    # 2. Eventos especiales que a veces mencionan banners en su texto
    if any(k in combined for k in ("nameless honor", "new eridu city fund", "pioneer podcast", "gnostic hymn", "battle pass")):
        return ("EVENTO", "Battle Pass")
    if any(k in combined for k in ("aptitude showcase", "test run", "ascendant aces")):
        return ("EVENTO", "Character Trial")

    # 3. Banners específicos por juego
    # ZZZ
    if default_type == "BANNER" or "is a w-engine channel" in wt_head or "w-engine channel" in combined:
        if "w-engine" in combined or "w-engine channel" in wt_head:
            return ("BANNER", "W-Engine Channel")
    if default_type == "BANNER" or "is an exclusive rescreening" in wt_head or "exclusive rescreening" in combined:
        if "rescreening" in combined or "rescreening" in wt_head:
            return ("BANNER", "Exclusive Rescreening")
    if default_type == "BANNER" or "is an exclusive channel" in wt_head or "exclusive channel" in combined:
        if "exclusive channel" in combined or "exclusive channel" in wt_head:
            return ("BANNER", "Exclusive Channel")
    if "bangboo channel" in combined or "bangboo channel" in wt_head:
        return ("BANNER", "Bangboo Channel")

    # HSR
    if "light cone event warp" in wt_head or "light cone event" in combined:
        return ("BANNER", "Light Cone Event Warp")
    if "character event warp" in wt_head or "character event" in combined:
        return ("BANNER", "Character Event Warp")
    if "collaboration warp" in wt_head or "collaboration warp" in combined:
        return ("BANNER", "Collaboration Warp")

    # WuWa
    if "featured weapon convene" in wt_head or "absolute pulsation" in combined or "weapon convene" in combined:
        return ("BANNER", "Featured Weapon Convene")
    if "featured resonator convene" in wt_head or "resonator convene" in combined:
        return ("BANNER", "Featured Resonator Convene")

    # Genshin Impact
    if "weapon event wish" in wt_head or "epitome invocation" in combined or "weapon event wish" in combined:
        return ("BANNER", "Weapon Event Wish")
    if "chronicled wish" in wt_head or "chronicled wish" in combined:
        return ("BANNER", "Chronicled Wish")
    if "character event wish" in wt_head or "character event wish" in combined:
        return ("BANNER", "Character Event Wish")

    # 4. Resto de subtipos de eventos
    if "final callback" in combined or "audition stage" in combined:
        return ("EVENTO", "Audition Stage")
    if "check-in" in combined or "check in" in combined:
        return ("EVENTO", "Check-In")
    if any(k in combined for k in ("login", "gift of odyssey", "all-new program", "gifts of ")):
        return ("EVENTO", "Login Event")
    if "web" in combined or "web event" in wt_head:
        return ("EVENTO", "Web Event")
    if "collaboration" in combined or "collab" in combined:
        return ("EVENTO", "Collaboration")
    if any(k in combined for k in (
        "planar fissure", "realm of the strange", "garden of plenty",
        "data bounty", "ley line overflow", "overflowing mastery",
        "chord cleansing", "bountiful crescendo"
    )):
        return ("EVENTO", "Double Drops")

    if default_type == "BANNER":
        return ("BANNER", default_subtype)
    return (default_type, default_subtype)


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
    if not raw:
        return None
    s = raw.strip()
    if any(skip in s.lower() for skip in ("indefinite", "tba", "unknown", "permanent", "to be announced")):
        return None

    detected_offset = explicit_offset
    if re.search(r"\bUTC\b", s, re.IGNORECASE) and not re.search(r"UTC[+-]", s, re.IGNORECASE):
        detected_offset = "+00:00"
    else:
        tz_match = re.search(r"(?:GMT|UTC)\s*([+-]\s*\d{1,2}(?::?\d{2})?)", s, re.IGNORECASE)
        if tz_match:
            detected_offset = parse_offset_str(tz_match.group(0))

    is_server_time = "server" in s.lower()

    cleaned = re.sub(r"\([^)]*\)", "", s)
    cleaned = re.sub(r"\b(?:GMT|UTC)\s*(?:[+-]\s*\d{1,2}(?::?\d{2})?)?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-–—")

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


def parse_date_range(text: str) -> Optional[tuple[str, str]]:
    s = re.sub(r"\s+", " ", text).strip()
    date_pat = r"(?:[A-Za-z]+\s+\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|\d{4}[/-]\d{1,2}[/-]\d{1,2}[^\s]*)"
    m_from_to = re.search(rf"\bFrom\s+({date_pat})\s+to\s+({date_pat})", s, flags=re.IGNORECASE)
    if m_from_to:
        return m_from_to.group(1).strip(), m_from_to.group(2).strip()

    parts = re.split(r"\s*[–—-]\s*", s, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None


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


def fetch_category_members(
    api_url: str,
    category: str,
    cmtype: str = "page",
    limit: int = 50,
    sort_recent: bool = False
) -> list[str]:
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmtype": cmtype,
        "cmlimit": str(limit),
        "format": "json"
    }
    if sort_recent:
        params["cmsort"] = "timestamp"
        params["cmdir"] = "desc"
    resp = requests.get(api_url, params=params, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    members = resp.json().get("query", {}).get("categorymembers", [])
    return [m["title"] for m in members]


def fetch_wikitext_batch(api_url: str, titles: list[str]) -> dict[str, str]:
    result = {}
    unique_titles = list(dict.fromkeys(titles))
    for i in range(0, len(unique_titles), 40):
        chunk = unique_titles[i:i + 40]
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
    params = {}
    for line in wikitext.splitlines():
        m = re.match(r"^\s*\|\s*([a-zA-Z0-9_ ]+?)\s*=\s*(.+?)\s*$", line)
        if m:
            key = m.group(1).strip().lower().replace(" ", "_")
            val = clean_wikitext_value(m.group(2))
            if val and key not in params:
                params[key] = val

    for m in re.finditer(r"\|\s*([a-zA-Z0-9_]+)\s*=\s*([^|}\n]+)", wikitext):
        key = m.group(1).strip().lower()
        val = clean_wikitext_value(m.group(2))
        if val and key not in params:
            params[key] = val
    return params


def extract_clean_cell_title(cell) -> tuple[str, Optional[str]]:
    best_page = None
    for a in cell.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/wiki/") and not re.match(r"^/wiki/(?:File|Image|Special):", href, re.IGNORECASE):
            page_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
            if not best_page:
                best_page = page_title
            text = a.get_text(" ", strip=True)
            if text and not text.lower().startswith("file:"):
                return clean_display_title(text), page_title

    raw = cell.get_text(" ", strip=True)
    return clean_display_title(raw), best_page


def is_relevant_window(end_iso: str) -> bool:
    try:
        clean = end_iso[:19]
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=10)
        return dt >= cutoff
    except Exception:
        return True


def resolve_version_bounds(
    api_url: str,
    version_str: str,
    version_cache: dict
) -> tuple[Optional[tuple[str, str]], Optional[tuple[str, str]]]:
    if version_str in version_cache:
        return version_cache[version_str]

    start_res = None
    end_res = None
    try:
        wts = fetch_wikitext_batch(api_url, [f"Version/{version_str}", f"Version {version_str}"])
        wt = next(iter(wts.values()), "")
        p = extract_template_params(wt)
        rel = p.get("release_date") or p.get("date") or p.get("start")
        end_d = p.get("end_date") or p.get("end")

        if rel:
            start_res = parse_datetime_value(rel, default_time="11:00:00", explicit_offset="+08:00")
        if not start_res:
            m_date = re.search(
                r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+202\d|\d{4}-\d{2}-\d{2})",
                wt
            )
            if m_date:
                start_res = parse_datetime_value(m_date.group(1), default_time="11:00:00", explicit_offset="+08:00")

        if end_d:
            end_res = parse_datetime_value(end_d, default_time="05:59:00", explicit_offset="+08:00")
        if not end_res and start_res:
            dt_s = datetime.strptime(start_res[0][:19], "%Y-%m-%dT%H:%M:%S")
            dt_e = (dt_s + timedelta(days=42)).replace(hour=5, minute=59, second=0)
            end_res = (dt_e.strftime("%Y-%m-%dT%H:%M:%S+08:00"), "GLOBAL")
    except Exception as e:
        print(f"Aviso resolviendo Version/{version_str}: {e}")

    version_cache[version_str] = (start_res, end_res)
    return start_res, end_res


def resolve_version_text(
    api_url: str,
    raw_text: str,
    is_end: bool,
    version_cache: dict
) -> Optional[tuple[str, str]]:
    m = re.search(r"Version\s+(\d+\.\d+)", raw_text, re.IGNORECASE)
    if not m:
        return None
    ver = m.group(1)
    s_ver, e_ver = resolve_version_bounds(api_url, ver, version_cache)
    return e_ver if is_end else s_ver


def parse_infobox_event(
    game_id: str,
    api_url: str,
    page_title: str,
    wikitext: str,
    version_cache: dict,
    override_display_title: Optional[str] = None,
    type_hint: str = "",
    default_type: str = "EVENTO",
    default_subtype: str = "In-Game Event"
) -> Optional[dict]:
    p = extract_template_params(wikitext)
    raw_type_field = f"{type_hint} {p.get('type', '')} {p.get('event_type', '')}"

    if should_ignore(f"{page_title} {raw_type_field}"):
        return None

    start_raw = p.get("time_start") or p.get("start") or p.get("date_start")
    end_raw = p.get("time_end") or p.get("end") or p.get("date_end")

    if not (start_raw and end_raw):
        dur_match = re.search(r"={2,3}\s*Duration\s*={2,3}\s*(.+)", wikitext, re.IGNORECASE)
        if dur_match:
            dr = parse_date_range(clean_wikitext_value(dur_match.group(1)))
            if dr:
                start_raw, end_raw = dr

    if not (start_raw and end_raw):
        return None

    start_offset = parse_offset_str(p.get("time_start_offset", ""))
    end_offset = parse_offset_str(p.get("time_end_offset", ""))

    parsed_start = parse_datetime_value(start_raw, default_time="11:00:00", explicit_offset=start_offset)
    if not parsed_start and "version" in start_raw.lower():
        parsed_start = resolve_version_text(api_url, start_raw, is_end=False, version_cache=version_cache)

    parsed_end = parse_datetime_value(end_raw, default_time="03:59:00", explicit_offset=end_offset)
    if not parsed_end and "version" in end_raw.lower():
        parsed_end = resolve_version_text(api_url, end_raw, is_end=True, version_cache=version_cache)

    if not parsed_start or not parsed_end:
        return None
    if not is_relevant_window(parsed_end[0]):
        return None

    display_title = clean_display_title(override_display_title or page_title)
    ev_type, sub_type = detect_types(
        title=f"{display_title} {page_title}",
        raw_type_hint=raw_type_field,
        wikitext=wikitext,
        default_type=default_type,
        default_subtype=default_subtype
    )

    return {
        "id": f"{game_id}-{slugify(display_title if 'File:' in page_title else page_title)}",
        "gameId": game_id,
        "title": display_title,
        "type": ev_type,
        "subType": sub_type,
        "startRaw": parsed_start[0],
        "startMode": parsed_start[1],
        "endRaw": parsed_end[0],
        "endMode": parsed_end[1],
        "colorHex": GAME_COLORS[game_id],
        "imageUrl": None
    }


# ==========================================
# SCRAPER GENÉRICO PARA WIKIS ESTÁNDAR (HSR, WUWA, GI)
# ==========================================

def scrape_standard_fandom(
    game_id: str,
    api_url: str,
    main_page: str,
    events_page: str,
    banner_list_page: str,
    default_banner_subtype: str,
    endgame_pages: list[tuple[str, str]],
    default_start_time: str = "04:00:00",
    banner_keyword: str = "warp"
) -> list[dict]:
    tag = game_id.upper()
    events_by_id: dict[str, dict] = {}
    hints: dict[str, dict] = {}
    version_cache: dict = {}

    def register_hint(
        page_title: str,
        display_title: Optional[str] = None,
        type_hint: str = "",
        default_type: str = "EVENTO",
        default_subtype: str = "In-Game Event"
    ):
        if should_ignore(f"{page_title} {type_hint}"):
            return
        if page_title.startswith(("Category:", "File:", "Template:", "Module:", "Special:")):
            return
        if page_title not in hints:
            hints[page_title] = {
                "display_title": display_title,
                "type_hint": type_hint,
                "default_type": default_type,
                "default_subtype": default_subtype,
            }

    # 1) Categorías Current / Upcoming / últimas 2 versiones / recientes
    for cat in ("Category:Current_Events", "Category:Upcoming_Content"):
        try:
            for t in fetch_category_members(api_url, cat, cmtype="page", limit=50):
                register_hint(t)
        except Exception as e:
            print(f"[{tag}] Aviso leyendo {cat}: {e}")

    try:
        ver_cats = fetch_category_members(api_url, "Category:Events_by_Version", cmtype="subcat", limit=80)
        parsed_vers = []
        for vc in ver_cats:
            m_ver = re.search(r"Version\s+(\d+)\.(\d+)\s+Events", vc, re.IGNORECASE)
            if m_ver:
                parsed_vers.append(((int(m_ver.group(1)), int(m_ver.group(2))), vc))
        parsed_vers.sort(reverse=True)
        for _, ver_cat in parsed_vers[:2]:
            for t in fetch_category_members(api_url, ver_cat, cmtype="page", limit=60):
                register_hint(t)
    except Exception as e:
        print(f"[{tag}] Aviso leyendo Events_by_Version: {e}")

    try:
        for t in fetch_category_members(api_url, "Category:Events", cmtype="page", limit=35, sort_recent=True):
            register_hint(t)
    except Exception as e:
        print(f"[{tag}] Aviso leyendo Category:Events: {e}")

    # 2) Portada principal (eventos activos y resets)
    try:
        main_soup = fetch_parse_html(api_url, main_page)
        if main_soup:
            for block in main_soup.select("li, tr, div"):
                txt = block.get_text(" ", strip=True)
                if "From " in txt and " to " in txt and len(txt) < 320:
                    dr = parse_date_range(txt)
                    title_text, linked_page = extract_clean_cell_title(block)
                    if dr and title_text and not should_ignore(title_text):
                        s_val = parse_datetime_value(dr[0], default_time=default_start_time)
                        e_val = parse_datetime_value(dr[1], default_time="03:59:00")
                        if s_val and e_val and is_relevant_window(e_val[0]):
                            ev_type, sub_type = detect_types(title_text, linked_page or "")
                            ev_id = f"{game_id}-{slugify(linked_page or title_text)}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": game_id,
                                "title": title_text,
                                "type": ev_type,
                                "subType": sub_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS[game_id],
                                "imageUrl": None
                            }
                            if linked_page:
                                register_hint(linked_page, display_title=title_text, default_type=ev_type, default_subtype=sub_type)
    except Exception as e:
        print(f"[{tag}] Error leyendo portada {main_page}: {e}")

    # 3) Página de tabla de Eventos
    try:
        ev_soup = fetch_parse_html(api_url, events_page)
        if ev_soup:
            for table in ev_soup.select("table"):
                for row in table.select("tr")[1:]:
                    cols = row.find_all(["td", "th"])
                    if len(cols) < 2:
                        continue
                    title_text, page_title = extract_clean_cell_title(cols[0])
                    duration_text = cols[1].get_text(" ", strip=True)
                    type_text = cols[2].get_text(" ", strip=True) if len(cols) > 2 else ""

                    if should_ignore(f"{title_text} {type_text}"):
                        continue

                    dr = parse_date_range(duration_text)
                    if dr and title_text:
                        s_val = parse_datetime_value(dr[0], default_time=default_start_time)
                        if not s_val and "version" in dr[0].lower():
                            s_val = resolve_version_text(api_url, dr[0], is_end=False, version_cache=version_cache)
                        e_val = parse_datetime_value(dr[1], default_time="03:59:00")
                        if not e_val and "version" in dr[1].lower():
                            e_val = resolve_version_text(api_url, dr[1], is_end=True, version_cache=version_cache)
                        if s_val and e_val and is_relevant_window(e_val[0]):
                            ev_type, sub_type = detect_types(title_text, type_text)
                            ev_id = f"{game_id}-{slugify(page_title or title_text)}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": game_id,
                                "title": title_text,
                                "type": ev_type,
                                "subType": sub_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS[game_id],
                                "imageUrl": None
                            }
                            if page_title:
                                register_hint(page_title, display_title=title_text, type_hint=type_text, default_type=ev_type, default_subtype=sub_type)
    except Exception as e:
        print(f"[{tag}] Error leyendo {events_page}: {e}")

    # 4) Tablas de Endgame (Stages/Floors/Seasons)
    for stage_page, mode_name in endgame_pages:
        try:
            st_soup = fetch_parse_html(api_url, stage_page)
            if not st_soup:
                continue
            for table in st_soup.select("table"):
                for row in table.select("tr")[1:]:
                    cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                    date_vals = []
                    for c in cols:
                        parsed = parse_datetime_value(c, default_time="04:00:00")
                        if parsed:
                            date_vals.append(parsed)
                    if len(date_vals) >= 2:
                        s_val, e_val = date_vals[0], date_vals[1]
                        if is_relevant_window(e_val[0]):
                            label = cols[0] if cols and not parse_datetime_value(cols[0]) else "Current"
                            title_full = f"{mode_name}: {label}" if label and label != mode_name else mode_name
                            ev_id = f"{game_id}-{slugify(mode_name)}-{slugify(s_val[0][:10])}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": game_id,
                                "title": title_full,
                                "type": "ENDGAME",
                                "subType": mode_name,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS[game_id],
                                "imageUrl": None
                            }
        except Exception as e:
            print(f"[{tag}] Aviso leyendo {stage_page}: {e}")

    # 5) Lista de Banners
    try:
        b_soup = fetch_parse_html(api_url, banner_list_page)
        if b_soup:
            for a in b_soup.select("a[href^='/wiki/']"):
                href = a.get("href", "")
                if re.search(r"/202\d-\d{2}-\d{2}$", href):
                    page_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                    register_hint(page_title, default_type="BANNER", default_subtype=default_banner_subtype)
    except Exception as e:
        print(f"[{tag}] Error leyendo {banner_list_page}: {e}")

    # 6) Procesamiento en lote del wikitext
    if hints:
        wikitexts = fetch_wikitext_batch(api_url, list(hints.keys()))
        for page_title, wt in wikitexts.items():
            h = hints.get(page_title, {})
            is_banner = h.get("default_type") == "BANNER" or (banner_keyword in wt[:500].lower())
            def_type = "BANNER" if is_banner else h.get("default_type", "EVENTO")
            def_sub = h.get("default_subtype", default_banner_subtype if is_banner else "In-Game Event")

            parsed = parse_infobox_event(
                game_id=game_id,
                api_url=api_url,
                page_title=page_title,
                wikitext=wt,
                version_cache=version_cache,
                override_display_title=h.get("display_title"),
                type_hint=h.get("type_hint", ""),
                default_type=def_type,
                default_subtype=def_sub
            )
            if parsed:
                events_by_id[parsed["id"]] = parsed

    deduped: dict[tuple[str, str], dict] = {}
    for ev in events_by_id.values():
        key = (ev["title"].lower(), ev["startRaw"][:10])
        existing = deduped.get(key)
        if not existing or (len(ev["startRaw"]) > len(existing["startRaw"])):
            deduped[key] = ev

    result = list(deduped.values())
    print(f"[{tag}] Eventos capturados: {len(result)}")
    return result


def scrape_hsr() -> list[dict]:
    return scrape_standard_fandom(
        game_id="hsr",
        api_url="https://honkai-star-rail.fandom.com/api.php",
        main_page="Honkai:_Star_Rail_Wiki",
        events_page="Events",
        banner_list_page="Warp/List",
        default_banner_subtype="Event Warp",
        endgame_pages=[
            ("Forgotten_Hall/Stages", "Memory of Chaos"),
            ("Pure_Fiction/Stages", "Pure Fiction"),
            ("Apocalyptic_Shadow/Stages", "Apocalyptic Shadow"),
            ("Anomaly_Arbitration/Stages", "Anomaly Arbitration"),
        ],
        default_start_time="04:00:00",
        banner_keyword="event warp"
    )


def scrape_wuwa() -> list[dict]:
    return scrape_standard_fandom(
        game_id="wuwa",
        api_url="https://wutheringwaves.fandom.com/api.php",
        main_page="Wuthering_Waves_Wiki",
        events_page="Event",
        banner_list_page="Convene/List",
        default_banner_subtype="Featured Resonator Convene",
        endgame_pages=[
            ("Tower_of_Adversity/Hazard_Zone", "Tower of Adversity"),
            ("Tower_of_Adversity/Stages", "Tower of Adversity"),
            ("Whimpering_Wastes/Stages", "Whimpering Wastes"),
            ("Whimpering_Wastes", "Whimpering Wastes"),
        ],
        default_start_time="10:00:00",
        banner_keyword="convene"
    )


def scrape_gi() -> list[dict]:
    return scrape_standard_fandom(
        game_id="gi",
        api_url="https://genshin-impact.fandom.com/api.php",
        main_page="Genshin_Impact_Wiki",
        events_page="Event",
        banner_list_page="Wish/List",
        default_banner_subtype="Character Event Wish",
        endgame_pages=[
            ("Spiral_Abyss/Floors", "Spiral Abyss"),
            ("Imaginarium_Theater/Seasons", "Imaginarium Theater"),
            ("Stygian_Onslaught/Seasons", "Stygian Onslaught"),
        ],
        default_start_time="10:00:00",
        banner_keyword="event wish"
    )


# ==========================================
# SCRAPER ZZZ (TIENE CANALES SEPARADOS)
# ==========================================

def scrape_zzz() -> list[dict]:
    api_url = "https://zenless-zone-zero.fandom.com/api.php"
    events_by_id: dict[str, dict] = {}
    linked_meta: dict[str, dict] = {}
    version_cache: dict = {}

    try:
        soup = fetch_parse_html(api_url, "Event")
        if soup:
            for table in soup.select("table"):
                for row in table.select("tr")[1:]:
                    cols = row.find_all(["td", "th"])
                    if len(cols) < 2:
                        continue
                    title_text, linked_page = extract_clean_cell_title(cols[0])
                    duration_text = cols[1].get_text(" ", strip=True)
                    type_text = cols[2].get_text(" ", strip=True) if len(cols) > 2 else ""

                    if should_ignore(f"{title_text} {type_text}"):
                        continue

                    dr = parse_date_range(duration_text)
                    if dr and title_text:
                        s_val = parse_datetime_value(dr[0], default_time="10:00:00")
                        e_val = parse_datetime_value(dr[1], default_time="03:59:00")
                        if s_val and e_val and is_relevant_window(e_val[0]):
                            ev_type, sub_type = detect_types(title_text, type_text)
                            fallback_id = f"zzz-{slugify(title_text)}"
                            events_by_id[fallback_id] = {
                                "id": fallback_id,
                                "gameId": "zzz",
                                "title": title_text,
                                "type": ev_type,
                                "subType": sub_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS["zzz"],
                                "imageUrl": None
                            }
                            if linked_page:
                                linked_meta[linked_page] = {
                                    "table_title": title_text,
                                    "fallback_id": fallback_id,
                                    "type_hint": type_text,
                                    "default_type": ev_type,
                                    "default_subtype": sub_type
                                }
    except Exception as e:
        print(f"[ZZZ] Error leyendo Event: {e}")

    channel_pages = [
        ("Exclusive_Channel", "Exclusive Channel"),
        ("W-Engine_Channel", "W-Engine Channel"),
        ("Bangboo_Channel", "Bangboo Channel"),
    ]
    for channel_page, subtype_name in channel_pages:
        try:
            csoup = fetch_parse_html(api_url, channel_page)
            if csoup:
                for a in csoup.select("a[href^='/wiki/']"):
                    href = a.get("href", "")
                    if re.search(r"/202\d-\d{2}-\d{2}$", href):
                        page_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                        linked_meta[page_title] = {
                            "table_title": None,
                            "fallback_id": None,
                            "type_hint": subtype_name,
                            "default_type": "BANNER",
                            "default_subtype": subtype_name
                        }
        except Exception as e:
            print(f"[ZZZ] Error leyendo {channel_page}: {e}")

    if linked_meta:
        wikitexts = fetch_wikitext_batch(api_url, list(linked_meta.keys()))
        for page_title, wt in wikitexts.items():
            meta = linked_meta.get(page_title, {})
            parsed = parse_infobox_event(
                game_id="zzz",
                api_url=api_url,
                page_title=page_title,
                wikitext=wt,
                version_cache=version_cache,
                override_display_title=meta.get("table_title"),
                type_hint=meta.get("type_hint", ""),
                default_type=meta.get("default_type", "EVENTO"),
                default_subtype=meta.get("default_subtype", "In-Game Event")
            )
            if parsed:
                fb_id = meta.get("fallback_id")
                if fb_id and fb_id in events_by_id:
                    del events_by_id[fb_id]
                events_by_id[parsed["id"]] = parsed

    print(f"[ZZZ] Eventos capturados: {len(events_by_id)}")
    return list(events_by_id.values())


# ==========================================
# ENSAMBLADO FINAL
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

    for game_id, scraper_fn in (
        ("hsr", scrape_hsr),
        ("zzz", scrape_zzz),
        ("wuwa", scrape_wuwa),
        ("gi", scrape_gi),
    ):
        try:
            items = scraper_fn()
            if items:
                final_events.extend(items)
            else:
                print(f"[{game_id.upper()}] Sin resultados nuevos, conservando datos previos.")
                final_events.extend([e for e in previous_events if e.get("gameId") == game_id])
        except Exception as e:
            print(f"[{game_id.upper()}] Fallo general: {e}")
            final_events.extend([e for e in previous_events if e.get("gameId") == game_id])

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
        print(f" - [{ev['gameId'].upper()}][{ev['type']} / {ev['subType']}] {ev['title']} ({ev['startRaw']} -> {ev['endRaw']})")


if __name__ == "__main__":
    main()
