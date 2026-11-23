import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "GachaEventTrackerBot/1.3 (Android personal tracker; MediaWiki API)"
}

GAME_COLORS = {
    "hsr": "#5C6BC0",
    "zzz": "#FF7043",
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


def detect_types(
    title: str,
    raw_type_hint: str = "",
    wikitext: str = "",
    default_type: str = "EVENTO",
    default_subtype: str = "In-Game Event"
) -> tuple[str, str]:
    combined = f"{title} {raw_type_hint}".lower()
    wt_head = wikitext[:700].lower()

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

    if "w-engine channel" in combined or "w-engine channel" in wt_head:
        return ("BANNER", "W-Engine Channel")
    if "exclusive rescreening" in combined or "exclusive rescreening" in wt_head:
        return ("BANNER", "Exclusive Rescreening")
    if "exclusive channel" in combined or "exclusive channel" in wt_head:
        return ("BANNER", "Exclusive Channel")
    if "bangboo channel" in combined or "bangboo channel" in wt_head:
        return ("BANNER", "Bangboo Channel")

    if "light cone event warp" in wt_head or "light cone event" in combined:
        return ("BANNER", "Light Cone Event Warp")
    if "character event warp" in wt_head or "character event" in combined:
        return ("BANNER", "Character Event Warp")
    if "collaboration warp" in wt_head or "collaboration warp" in combined:
        return ("BANNER", "Collaboration Warp")

    if "nameless honor" in combined or "new eridu city fund" in combined or "battle pass" in combined:
        return ("EVENTO", "Battle Pass")
    if "final callback" in combined or "audition stage" in combined:
        return ("EVENTO", "Audition Stage")
    if "check-in" in combined or "check in" in combined:
        return ("EVENTO", "Check-In")
    if "login" in combined or "gift of odyssey" in combined:
        return ("EVENTO", "Login Event")
    if "web" in combined:
        return ("EVENTO", "Web Event")
    if "collaboration" in combined or "collab" in combined:
        return ("EVENTO", "Collaboration")
    if any(k in combined for k in ("planar fissure", "realm of the strange", "garden of plenty", "data bounty")):
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


def fetch_category_titles(api_url: str, category: str, limit: int = 50) -> list[str]:
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
    for m in re.finditer(r"\|\s*([a-zA-Z0-9_ ]+?)\s*=\s*([^|}\n]+)", wikitext):
        key = m.group(1).strip().lower().replace(" ", "_")
        val = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", m.group(2).strip())
        val = re.sub(r"\{\{.*?\}\}", "", val).strip()
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


def resolve_version_start(
    api_url: str,
    raw_start: str,
    version_cache: dict[str, tuple[str, str]]
) -> Optional[tuple[str, str]]:
    m = re.search(r"Version\s+(\d+\.\d+)", raw_start, re.IGNORECASE)
    if not m:
        return None
    ver = m.group(1)
    if ver in version_cache:
        return version_cache[ver]

    try:
        wts = fetch_wikitext_batch(api_url, [f"Version/{ver}"])
        wt = next(iter(wts.values()), "")
        p = extract_template_params(wt)
        rel = p.get("release_date") or p.get("date") or p.get("start")
        if rel:
            parsed = parse_datetime_value(rel, default_time="11:00:00", explicit_offset="+08:00")
            if parsed:
                version_cache[ver] = parsed
                return parsed
        m_date = re.search(
            r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+202\d)",
            wt
        )
        if m_date:
            parsed = parse_datetime_value(m_date.group(1), default_time="11:00:00", explicit_offset="+08:00")
            if parsed:
                version_cache[ver] = parsed
                return parsed
    except Exception as e:
        print(f"Aviso resolviendo Version/{ver}: {e}")
    return None


def parse_infobox_event(
    game_id: str,
    api_url: str,
    page_title: str,
    wikitext: str,
    version_cache: dict[str, tuple[str, str]],
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
            dr = parse_date_range(dur_match.group(1))
            if dr:
                start_raw, end_raw = dr

    if not (start_raw and end_raw):
        return None

    start_offset = parse_offset_str(p.get("time_start_offset", ""))
    end_offset = parse_offset_str(p.get("time_end_offset", ""))

    parsed_start = parse_datetime_value(start_raw, default_time="11:00:00", explicit_offset=start_offset)
    if not parsed_start and "version" in start_raw.lower():
        parsed_start = resolve_version_start(api_url, start_raw, version_cache)

    parsed_end = parse_datetime_value(end_raw, default_time="03:59:00", explicit_offset=end_offset)

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


def scrape_hsr() -> list[dict]:
    api_url = "https://honkai-star-rail.fandom.com/api.php"
    events_by_id: dict[str, dict] = {}
    hints: dict[str, dict] = {}
    version_cache: dict[str, tuple[str, str]] = {}

    def register_hint(
        page_title: str,
        display_title: Optional[str] = None,
        type_hint: str = "",
        default_type: str = "EVENTO",
        default_subtype: str = "In-Game Event"
    ):
        if should_ignore(f"{page_title} {type_hint}"):
            return
        if page_title not in hints:
            hints[page_title] = {
                "display_title": display_title,
                "type_hint": type_hint,
                "default_type": default_type,
                "default_subtype": default_subtype,
            }

    for cat in ("Category:Current_Events", "Category:Upcoming_Content"):
        try:
            for t in fetch_category_titles(api_url, cat, limit=50):
                register_hint(t)
        except Exception as e:
            print(f"[HSR] Error leyendo {cat}: {e}")

    try:
        main_soup = fetch_parse_html(api_url, "Honkai:_Star_Rail_Wiki")
        if main_soup:
            for block in main_soup.select("li, tr, div"):
                txt = block.get_text(" ", strip=True)
                if "From " in txt and " to " in txt and len(txt) < 320:
                    dr = parse_date_range(txt)
                    title_text, linked_page = extract_clean_cell_title(block)
                    if dr and title_text and not should_ignore(title_text):
                        s_val = parse_datetime_value(dr[0], default_time="04:00:00")
                        e_val = parse_datetime_value(dr[1], default_time="03:59:00")
                        if s_val and e_val and is_relevant_window(e_val[0]):
                            ev_type, sub_type = detect_types(title_text, linked_page or "")
                            ev_id = f"hsr-{slugify(linked_page or title_text)}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": "hsr",
                                "title": title_text,
                                "type": ev_type,
                                "subType": sub_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS["hsr"],
                                "imageUrl": None
                            }
                            if linked_page:
                                register_hint(linked_page, display_title=title_text, default_type=ev_type, default_subtype=sub_type)
    except Exception as e:
        print(f"[HSR] Error leyendo portada: {e}")

    try:
        ev_soup = fetch_parse_html(api_url, "Events")
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
                        s_val = parse_datetime_value(dr[0], default_time="04:00:00")
                        if not s_val and "version" in dr[0].lower():
                            s_val = resolve_version_start(api_url, dr[0], version_cache)
                        e_val = parse_datetime_value(dr[1], default_time="03:59:00")
                        if s_val and e_val and is_relevant_window(e_val[0]):
                            ev_type, sub_type = detect_types(title_text, type_text)
                            ev_id = f"hsr-{slugify(page_title or title_text)}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": "hsr",
                                "title": title_text,
                                "type": ev_type,
                                "subType": sub_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS["hsr"],
                                "imageUrl": None
                            }
                            if page_title:
                                register_hint(page_title, display_title=title_text, type_hint=type_text, default_type=ev_type, default_subtype=sub_type)
    except Exception as e:
        print(f"[HSR] Error leyendo Events: {e}")

    endgame_pages = [
        ("Forgotten_Hall/Stages", "Memory of Chaos"),
        ("Pure_Fiction/Stages", "Pure Fiction"),
        ("Apocalyptic_Shadow/Stages", "Apocalyptic Shadow"),
        ("Anomaly_Arbitration/Stages", "Anomaly Arbitration"),
    ]
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
                            ev_id = f"hsr-{slugify(mode_name)}-{slugify(s_val[0][:10])}"
                            events_by_id[ev_id] = {
                                "id": ev_id,
                                "gameId": "hsr",
                                "title": title_full,
                                "type": "ENDGAME",
                                "subType": mode_name,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS["hsr"],
                                "imageUrl": None
                            }
        except Exception as e:
            print(f"[HSR] Error leyendo {stage_page}: {e}")

    try:
        warp_soup = fetch_parse_html(api_url, "Warp/List")
        if warp_soup:
            for a in warp_soup.select("a[href^='/wiki/']"):
                href = a.get("href", "")
                if re.search(r"/202\d-\d{2}-\d{2}$", href):
                    page_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                    register_hint(page_title, default_type="BANNER", default_subtype="Event Warp")
    except Exception as e:
        print(f"[HSR] Error leyendo Warp/List: {e}")

    if hints:
        wikitexts = fetch_wikitext_batch(api_url, list(hints.keys()))
        for page_title, wt in wikitexts.items():
            h = hints.get(page_title, {})
            is_warp = bool(re.search(r"/202\d-\d{2}-\d{2}$", page_title)) and "warp" in wt.lower()
            def_type = "BANNER" if is_warp else h.get("default_type", "EVENTO")
            def_sub = h.get("default_subtype", "Event Warp" if is_warp else "In-Game Event")

            parsed = parse_infobox_event(
                game_id="hsr",
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

    print(f"[HSR] Eventos capturados: {len(events_by_id)}")
    return list(events_by_id.values())


def scrape_zzz() -> list[dict]:
    api_url = "https://zenless-zone-zero.fandom.com/api.php"
    events_by_id: dict[str, dict] = {}
    linked_meta: dict[str, dict] = {}
    version_cache: dict[str, tuple[str, str]] = {}

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

    for game_id, scraper_fn in (("hsr", scrape_hsr), ("zzz", scrape_zzz)):
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
