import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HEADERS = {
    "User-Agent": "GachaEventTrackerBot/1.9 (Android personal tracker; MediaWiki API)"
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

GAME_COLORS = {
    "hsr": "#5C6BC0",
    "zzz": "#FF7043",
    "wuwa": "#26A69A",
    "gi": "#AB47BC",
}

GAMES_CATALOG = [
    {"id": "hsr", "name": "Honkai: Star Rail", "shortName": "HSR", "colorHex": "#5C6BC0"},
    {"id": "zzz", "name": "Zenless Zone Zero", "shortName": "ZZZ", "colorHex": "#FF7043"},
    {"id": "wuwa", "name": "Wuthering Waves", "shortName": "WuWa", "colorHex": "#26A69A"},
    {"id": "gi", "name": "Genshin Impact", "shortName": "GI", "colorHex": "#AB47BC"},
]

IGNORED_KEYWORDS = ("social media", "fan art", "fanart", "community event")

GENERIC_GAME8_LABELS = {
    "event duration", "duration", "period", "schedule", "date", "dates",
    "time", "availability", "release date", "start & end", "event period",
    "active period", "reset schedule", "cycle", "current cycle", "patch duration",
    "banner duration", "endgame duration", "schedule & duration"
}

DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%B %d, %Y %I:%M:%S %p",
    "%B %d, %Y %H:%M:%S",
    "%B %d, %Y %H:%M",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y %H:%M:%S",
    "%b %d, %Y %H:%M",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B, %Y %H:%M:%S",
    "%d %B, %Y %H:%M",
    "%d %B, %Y",
    "%d %B %Y %H:%M:%S",
    "%d %B %Y %H:%M",
    "%d %B %Y",
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


def normalize_image_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    if not u.startswith("http"):
        return None
    if "data:image" in u or "spacer" in u.lower():
        return None
    if "static.wikia.nocookie.net" in u:
        u = re.sub(r"/scale-to-width-down/\d+", "/scale-to-width-down/450", u)
    return u


def extract_img_from_html_node(node) -> Optional[str]:
    if not node:
        return None
    for img in node.select("img"):
        src = img.get("data-src") or img.get("data-image-name") or img.get("src") or ""
        if not src.startswith("http"):
            continue
        low = src.lower()
        if any(skip in low for skip in ("1x1", "spacer", "blank", "item_primogem", "item_stellar_jade", "polychrome")):
            continue
        return normalize_image_url(src)
    return None


def extract_infobox_image_filename(wikitext: str) -> Optional[str]:
    """Busca |image = Nombre.png o la primera imagen dentro de un <gallery> de infobox."""
    m = re.search(
        r"\|\s*(?:image|image1|banner|header|cover)\s*=\s*(.+?)(?=\n\s*\||\n\s*\}\}|\Z)",
        wikitext,
        flags=re.IGNORECASE | re.DOTALL
    )
    if m:
        block = m.group(1)
        m_file = re.search(r"([A-Za-z0-9 _'\":,()\-\.!]+\.(?:png|jpe?g|webp))", block, flags=re.IGNORECASE)
        if m_file:
            fname = m_file.group(1).strip()
            fname = re.sub(r"^(?:File|Image):", "", fname, flags=re.IGNORECASE).strip()
            return f"File:{fname}"
    return None


def fetch_fandom_file_urls(api_url: str, file_titles: list[str]) -> dict[str, str]:
    """Convierte nombres 'File:Banner.png' en URLs CDN reales de 450px en lote."""
    result = {}
    unique_files = list(dict.fromkeys([f for f in file_titles if f]))
    for i in range(0, len(unique_files), 40):
        chunk = unique_files[i:i + 40]
        params = {
            "action": "query",
            "titles": "|".join(chunk),
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": "450",
            "format": "json",
            "redirects": "1"
        }
        try:
            resp = requests.get(api_url, params=params, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            for page_data in pages.values():
                title = page_data.get("title")
                ii = page_data.get("imageinfo", [])
                if title and ii:
                    url = ii[0].get("thumburl") or ii[0].get("url")
                    norm = normalize_image_url(url)
                    if norm:
                        result[title.lower()] = norm
        except Exception as e:
            print(f"Aviso imageinfo: {e}")
    return result


def fetch_page_thumbnails(api_url: str, page_titles: list[str]) -> dict[str, str]:
    """Fallback con prop=pageimages para páginas que no tienen |image= explícito."""
    result = {}
    unique_pages = list(dict.fromkeys([p for p in page_titles if p]))
    for i in range(0, len(unique_pages), 40):
        chunk = unique_pages[i:i + 40]
        params = {
            "action": "query",
            "titles": "|".join(chunk),
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": "450",
            "format": "json",
            "redirects": "1"
        }
        try:
            resp = requests.get(api_url, params=params, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            for page_data in pages.values():
                title = page_data.get("title")
                thumb = page_data.get("thumbnail", {}).get("source")
                norm = normalize_image_url(thumb)
                if title and norm:
                    result[title.lower()] = norm
        except Exception as e:
            print(f"Aviso pageimages: {e}")
    return result


def detect_types(
    title: str,
    raw_type_hint: str = "",
    wikitext: str = "",
    default_type: str = "EVENTO",
    default_subtype: str = "In-Game Event"
) -> tuple[str, str]:
    combined = f"{title} {raw_type_hint}".lower()
    wt_head = clean_wikitext_value(wikitext[:2500]).lower()

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
    if "endstate matrix" in combined:
        return ("ENDGAME", "Endstate Matrix")
    if "fantasies of the thousand gateways" in combined or "thousand gateways" in combined:
        return ("ENDGAME", "Thousand Gateways")

    if "spiral abyss" in combined:
        return ("ENDGAME", "Spiral Abyss")
    if "imaginarium theater" in combined:
        return ("ENDGAME", "Imaginarium Theater")
    if "stygian onslaught" in combined:
        return ("ENDGAME", "Stygian Onslaught")

    if any(k in combined for k in ("nameless honor", "new eridu city fund", "pioneer podcast", "gnostic hymn", "battle pass")):
        return ("EVENTO", "Battle Pass")
    if any(k in combined for k in ("aptitude showcase", "test run", "ascendant aces")):
        return ("EVENTO", "Character Trial")

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

    if "weapon event wish" in wt_head or "epitome invocation" in combined or "weapon event wish" in combined:
        return ("BANNER", "Weapon Event Wish")
    if "chronicled wish" in wt_head or "chronicled wish" in combined:
        return ("BANNER", "Chronicled Wish")
    if "character event wish" in wt_head or "character event wish" in combined:
        return ("BANNER", "Character Event Wish")

    if "light cone event warp" in wt_head or "light cone event warp" in combined:
        return ("BANNER", "Light Cone Event Warp")
    if "character event warp" in wt_head or "character event warp" in combined:
        return ("BANNER", "Character Event Warp")
    if "collaboration warp" in wt_head or "collaboration warp" in combined:
        return ("BANNER", "Collaboration Warp")

    if "featured weapon convene" in wt_head or "absolute pulsation" in combined or "weapon convene" in combined:
        return ("BANNER", "Featured Weapon Convene")
    if "featured resonator convene" in wt_head or "resonator convene" in combined:
        return ("BANNER", "Featured Resonator Convene")

    if "final callback" in combined or "audition stage" in combined:
        return ("EVENTO", "Audition Stage")
    if "check-in" in combined or "check in" in combined:
        return ("EVENTO", "Check-In")
    if any(k in combined for k in ("login", "7-day", "gift of odyssey", "all-new program", "gifts of ", "gift of melodic")):
        return ("EVENTO", "Login Event")
    if "web" in combined or "web event" in wt_head:
        return ("EVENTO", "Web Event")
    if "collaboration" in combined or "collab" in combined:
        return ("EVENTO", "Collaboration")
    if any(k in combined for k in (
        "planar fissure", "realm of the strange", "garden of plenty",
        "data bounty", "ley line overflow", "overflowing mastery",
        "chord cleansing", "bountiful crescendo", "bounty of the wilds", "lollo campaign"
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
    if any(skip in s.lower() for skip in ("indefinite", "tba", "unknown", "permanent", "to be announced", "no end")):
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
    cleaned = re.sub(r"\b([A-Za-z]{3})\.", r"\1", cleaned)
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
    s = re.sub(r"^(?:event duration|duration|period|schedule|date|time)\s*:\s*", "", s, flags=re.IGNORECASE)

    date_pat = r"(?:[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|\d{4}[/-]\d{1,2}[/-]\d{1,2}[^\s]*|\d{1,2}/\d{1,2}/\d{4})"
    m_from_to = re.search(rf"\bFrom\s+({date_pat})\s+to\s+({date_pat})", s, flags=re.IGNORECASE)
    if m_from_to:
        return m_from_to.group(1).strip(), m_from_to.group(2).strip()

    m_short_yr = re.search(
        r"([A-Za-z]{3,9}\.?\s+\d{1,2})\s*[–—~-]\s*([A-Za-z]{3,9}\.?\s+\d{1,2}),?\s+(\d{4})",
        s
    )
    if m_short_yr:
        year = m_short_yr.group(3)
        return f"{m_short_yr.group(1)}, {year}", f"{m_short_yr.group(2)}, {year}"

    m_no_yr = re.search(
        r"\b([A-Za-z]{3,9}\.?\s+\d{1,2})\s*[–—~-]\s*([A-Za-z]{3,9}\.?\s+\d{1,2})\b",
        s
    )
    if m_no_yr:
        yr = datetime.now(timezone.utc).year
        return f"{m_no_yr.group(1)}, {yr}", f"{m_no_yr.group(2)}, {yr}"

    m_num_no_yr = re.search(r"\b(\d{1,2}/\d{1,2})\s*[–—~-]\s*(\d{1,2}/\d{1,2})\b", s)
    if m_num_no_yr:
        yr = datetime.now(timezone.utc).year
        return f"{m_num_no_yr.group(1)}/{yr}", f"{m_num_no_yr.group(2)}/{yr}"

    m_full = re.search(rf"({date_pat})\s*[–—~-]\s*({date_pat})", s)
    if m_full:
        return m_full.group(1).strip(), m_full.group(2).strip()

    parts = re.split(r"\s*[–—~-]\s*", s, maxsplit=1)
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
    default_subtype: str = "In-Game Event",
    image_url: Optional[str] = None
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
        "imageUrl": image_url
    }


def resolve_images_for_pages(api_url: str, wikitexts: dict[str, str]) -> dict[str, str]:
    """Obtiene la mejor URL de imagen para cada página (infobox |image= o pageimages)."""
    page_to_file: dict[str, str] = {}
    pages_without_file: list[str] = []

    for title, wt in wikitexts.items():
        fname = extract_infobox_image_filename(wt)
        if fname:
            page_to_file[title] = fname
        else:
            pages_without_file.append(title)

    file_urls = fetch_fandom_file_urls(api_url, list(page_to_file.values()))
    thumb_urls = fetch_page_thumbnails(api_url, list(wikitexts.keys()))

    resolved: dict[str, str] = {}
    for title in wikitexts.keys():
        fname = page_to_file.get(title)
        url = file_urls.get(fname.lower()) if fname else None
        if not url:
            url = thumb_urls.get(title.lower())
        if url:
            resolved[title] = url
    return resolved


def scrape_standard_fandom(
    game_id: str,
    api_url: str,
    main_page: str,
    events_pages: list[str],
    banner_list_page: str,
    default_banner_subtype: str,
    endgame_pages: list[tuple[str, str]],
    default_start_time: str = "04:00:00",
    banner_keyword: str = "event warp"
) -> list[dict]:
    tag = game_id.upper()
    events_by_id: dict[str, dict] = {}
    hints: dict[str, dict] = {}
    version_cache: dict = {}
    season_image: Optional[str] = None

    def register_hint(
        page_title: str,
        display_title: Optional[str] = None,
        type_hint: str = "",
        default_type: str = "EVENTO",
        default_subtype: str = "In-Game Event",
        html_image: Optional[str] = None
    ):
        if should_ignore(f"{page_title} {type_hint}"):
            return
        if page_title.startswith(("Category:", "File:", "Template:", "Module:", "Special:")):
            return
        existing = hints.get(page_title)
        if not existing or (default_type == "BANNER" and existing.get("default_type") != "BANNER"):
            hints[page_title] = {
                "display_title": display_title or (existing.get("display_title") if existing else None),
                "type_hint": type_hint or (existing.get("type_hint", "") if existing else ""),
                "default_type": default_type,
                "default_subtype": default_subtype,
                "html_image": html_image or (existing.get("html_image") if existing else None),
            }
        elif html_image and not existing.get("html_image"):
            existing["html_image"] = html_image

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
                parsed_vers.append(((int(m_ver.group(1)), int(m_ver.group(2))), vc, f"{m_ver.group(1)}.{m_ver.group(2)}"))
        parsed_vers.sort(reverse=True)
        for _, ver_cat, _ in parsed_vers[:2]:
            for t in fetch_category_members(api_url, ver_cat, cmtype="page", limit=60):
                register_hint(t)
        if parsed_vers:
            latest_ver = parsed_vers[0][2]
            ver_thumbs = fetch_page_thumbnails(api_url, [f"Version/{latest_ver}", f"Version {latest_ver}"])
            if ver_thumbs:
                season_image = next(iter(ver_thumbs.values()))
    except Exception as e:
        print(f"[{tag}] Aviso leyendo Events_by_Version: {e}")

    try:
        for t in fetch_category_members(api_url, "Category:Events", cmtype="page", limit=35, sort_recent=True):
            register_hint(t)
    except Exception as e:
        print(f"[{tag}] Aviso leyendo Category:Events: {e}")

    try:
        main_soup = fetch_parse_html(api_url, main_page)
        if main_soup:
            for block in main_soup.select("li, tr, div"):
                txt = block.get_text(" ", strip=True)
                if "From " in txt and " to " in txt and len(txt) < 320:
                    dr = parse_date_range(txt)
                    title_text, linked_page = extract_clean_cell_title(block)
                    img_url = extract_img_from_html_node(block)
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
                                "imageUrl": img_url
                            }
                            if linked_page:
                                register_hint(linked_page, display_title=title_text, default_type=ev_type, default_subtype=sub_type, html_image=img_url)
    except Exception as e:
        print(f"[{tag}] Aviso leyendo portada {main_page}: {e}")

    for events_page in events_pages:
        try:
            ev_soup = fetch_parse_html(api_url, events_page)
            if not ev_soup:
                continue
            for table in ev_soup.select("table"):
                for row in table.select("tr")[1:]:
                    cols = row.find_all(["td", "th"])
                    if len(cols) < 2:
                        continue
                    title_text, page_title = extract_clean_cell_title(cols[0])
                    duration_text = cols[1].get_text(" ", strip=True)
                    type_text = cols[2].get_text(" ", strip=True) if len(cols) > 2 else ""
                    img_url = extract_img_from_html_node(cols[0]) or extract_img_from_html_node(row)

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
                                "imageUrl": img_url
                            }
                            if page_title:
                                register_hint(page_title, display_title=title_text, type_hint=type_text, default_type=ev_type, default_subtype=sub_type, html_image=img_url)
        except Exception as e:
            print(f"[{tag}] Aviso leyendo {events_page}: {e}")

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
                                "imageUrl": extract_img_from_html_node(row)
                            }

            for a in st_soup.select("a[href^='/wiki/']"):
                href = a.get("href", "")
                if re.search(r"[/_](2026|2027|2028)-\d{2}-\d{2}$", href):
                    sub_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                    register_hint(sub_title, display_title=mode_name, default_type="ENDGAME", default_subtype=mode_name)
        except Exception as e:
            print(f"[{tag}] Aviso leyendo {stage_page}: {e}")

    try:
        b_soup = fetch_parse_html(api_url, banner_list_page)
        if b_soup:
            for a in b_soup.select("a[href^='/wiki/']"):
                href = a.get("href", "")
                if re.search(r"[/_]202\d-\d{2}-\d{2}$", href):
                    page_title = requests.utils.unquote(href.replace("/wiki/", "")).replace("_", " ")
                    register_hint(page_title, default_type="BANNER", default_subtype=default_banner_subtype)
    except Exception as e:
        print(f"[{tag}] Error leyendo {banner_list_page}: {e}")

    if hints:
        wikitexts = fetch_wikitext_batch(api_url, list(hints.keys()))
        page_images = resolve_images_for_pages(api_url, wikitexts)

        for page_title, wt in wikitexts.items():
            h = hints.get(page_title, {})
            is_banner = h.get("default_type") == "BANNER" or (banner_keyword in wt[:500].lower())
            def_type = "BANNER" if is_banner else h.get("default_type", "EVENTO")
            def_sub = default_banner_subtype if (is_banner and h.get("default_type") != "BANNER") else h.get("default_subtype", "In-Game Event")

            img = page_images.get(page_title) or h.get("html_image")
            parsed = parse_infobox_event(
                game_id=game_id,
                api_url=api_url,
                page_title=page_title,
                wikitext=wt,
                version_cache=version_cache,
                override_display_title=h.get("display_title"),
                type_hint=h.get("type_hint", ""),
                default_type=def_type,
                default_subtype=def_sub,
                image_url=img
            )
            if parsed:
                events_by_id[parsed["id"]] = parsed

    deduped: dict[tuple[str, str], dict] = {}
    for ev in events_by_id.values():
        key = (ev["title"].lower(), ev["startRaw"][:10])
        existing = deduped.get(key)
        if not existing or (len(ev["startRaw"]) > len(existing["startRaw"])):
            deduped[key] = ev
        elif existing and not existing.get("imageUrl") and ev.get("imageUrl"):
            existing["imageUrl"] = ev["imageUrl"]

    result = list(deduped.values())

    # Imagen de temporada por defecto para eventos que no tenían imagen propia
    if not season_image:
        for ev in result:
            if ev.get("type") == "BANNER" and ev.get("imageUrl"):
                season_image = ev["imageUrl"]
                break
    if season_image:
        for ev in result:
            if not ev.get("imageUrl"):
                ev["imageUrl"] = season_image

    print(f"[{tag}] Eventos capturados (Fandom): {len(result)}")
    return result


def scrape_wuwa_game8() -> list[dict]:
    pages_to_scan = [
        ("https://game8.co/games/Wuthering-Waves/archives/453473", None, None),
        ("https://game8.co/games/Wuthering-Waves/archives/457211", "ENDGAME", "Tower of Adversity"),
        ("https://game8.co/games/Wuthering-Waves/archives/501122", "ENDGAME", "Whimpering Wastes"),
    ]
    events_map: dict[tuple[str, str], dict] = {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent=BROWSER_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )
            page = context.new_page()

            for url, forced_type, forced_subtype in pages_to_scan:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=35000)
                    try:
                        page.wait_for_selector("table, h1", timeout=15000)
                    except Exception:
                        page.wait_for_timeout(3000)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    tables = soup.select("table")
                    print(f"[WUWA-Game8] {url.split('/')[-1]} -> {len(tables)} tablas")

                    for table in tables:
                        prev_heading = table.find_previous(["h3", "h2", "h4"])
                        heading_raw = prev_heading.get_text(" ", strip=True) if prev_heading else ""
                        if heading_raw.lower().strip(": ") in (
                            "overview", "details", "event details", "schedule",
                            "rewards", "event rewards", "how to unlock", "how to play"
                        ):
                            prev_h3 = table.find_previous(["h3", "h2"])
                            if prev_h3:
                                heading_raw = prev_h3.get_text(" ", strip=True)

                        heading_low = heading_raw.lower()
                        if any(skip in heading_low for skip in ("permanent", "past", "ended", "archive", "previous", "history", "reward")):
                            continue

                        # En Game8 la imagen suele estar justo arriba de la mini-tabla o dentro de ella
                        heading_img = None
                        if prev_heading:
                            nxt_img = prev_heading.find_next("img")
                            if nxt_img:
                                src = nxt_img.get("data-src") or nxt_img.get("src") or ""
                                heading_img = normalize_image_url(src)

                        for row in table.select("tr"):
                            cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                            if len(cols) < 2:
                                continue
                            if cols[0].lower() in ("event", "name", "event name", "phase") and cols[1].lower() in ("duration", "dates", "period", "time"):
                                continue

                            dr = None
                            dr_idx = -1
                            for idx, col_txt in enumerate(cols):
                                maybe_dr = parse_date_range(col_txt)
                                if maybe_dr and parse_datetime_value(maybe_dr[0]) and parse_datetime_value(maybe_dr[1]):
                                    dr = maybe_dr
                                    dr_idx = idx
                                    break

                            if not dr:
                                continue

                            first_col_clean = cols[0].lower().strip(": ")
                            if dr_idx != 0 and first_col_clean not in GENERIC_GAME8_LABELS and len(cols[0]) > 2:
                                raw_title = cols[0]
                            else:
                                raw_title = heading_raw

                            clean_title = re.sub(
                                r"\s*(?:\((?:Ongoing|Upcoming|New|Ended)\)|Guide|Event Guide|Reset Time.*|Schedule.*)$",
                                "",
                                raw_title,
                                flags=re.IGNORECASE
                            ).strip(" :-")

                            if forced_subtype:
                                clean_title = forced_subtype

                            if not clean_title or clean_title.lower().strip(": ") in GENERIC_GAME8_LABELS:
                                continue

                            extra_col = " ".join(c for i, c in enumerate(cols) if i != dr_idx)
                            if should_ignore(f"{clean_title} {extra_col}"):
                                continue

                            s_val = parse_datetime_value(dr[0], default_time="10:00:00")
                            e_val = parse_datetime_value(dr[1], default_time="03:59:00")
                            if not (s_val and e_val) or not is_relevant_window(e_val[0]):
                                continue

                            if forced_type and forced_subtype:
                                ev_type, sub_type = forced_type, forced_subtype
                            else:
                                ev_type, sub_type = detect_types(clean_title, f"{heading_low} {extra_col}")

                            row_img = extract_img_from_html_node(row) or heading_img
                            key = (clean_title.lower(), s_val[0][:10])
                            events_map[key] = {
                                "id": f"wuwa-g8-{slugify(clean_title)}-{s_val[0][:10]}",
                                "gameId": "wuwa",
                                "title": clean_title,
                                "type": ev_type,
                                "subType": sub_type,
                                "startRaw": s_val[0],
                                "startMode": s_val[1],
                                "endRaw": e_val[0],
                                "endMode": e_val[1],
                                "colorHex": GAME_COLORS["wuwa"],
                                "imageUrl": row_img
                            }
                except Exception as page_err:
                    print(f"[WUWA-Game8] Aviso en {url}: {page_err}")

            browser.close()
    except Exception as e:
        print(f"[WUWA-Game8] Error Playwright: {e}")

    events = list(events_map.values())
    print(f"[WUWA-Game8] Eventos extraídos: {len(events)}")
    return events


def scrape_hsr() -> list[dict]:
    return scrape_standard_fandom(
        game_id="hsr",
        api_url="https://honkai-star-rail.fandom.com/api.php",
        main_page="Honkai:_Star_Rail_Wiki",
        events_pages=["Events"],
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
    fandom_items = scrape_standard_fandom(
        game_id="wuwa",
        api_url="https://wutheringwaves.fandom.com/api.php",
        main_page="Wuthering_Waves_Wiki",
        events_pages=["Events", "Event", "Event/History", "Event/History/Version_3"],
        banner_list_page="Convene/List",
        default_banner_subtype="Featured Resonator Convene",
        endgame_pages=[
            ("Tower_of_Adversity/Floors", "Tower of Adversity"),
            ("Whimpering_Wastes", "Whimpering Wastes"),
            ("Endstate_Matrix", "Endstate Matrix"),
        ],
        default_start_time="10:00:00",
        banner_keyword="resonator convene"
    )
    game8_items = scrape_wuwa_game8()

    # Imagen de temporada desde los banners de Fandom para los eventos de Game8 que no tengan foto
    wuwa_season_img = next((ev["imageUrl"] for ev in fandom_items if ev.get("imageUrl")), None)

    combined: dict[tuple[str, str], dict] = {}
    for ev in game8_items + fandom_items:
        if not ev.get("imageUrl") and wuwa_season_img:
            ev["imageUrl"] = wuwa_season_img
        key = (ev["title"].lower(), ev["startRaw"][:10])
        combined[key] = ev

    result = list(combined.values())
    print(f"[WUWA] Total combinado: {len(result)}")
    return result


def scrape_gi() -> list[dict]:
    return scrape_standard_fandom(
        game_id="gi",
        api_url="https://genshin-impact.fandom.com/api.php",
        main_page="Genshin_Impact_Wiki",
        events_pages=["Event", "Event/History"],
        banner_list_page="Wish/List",
        default_banner_subtype="Character Event Wish",
        endgame_pages=[
            ("Spiral_Abyss/Floors", "Spiral Abyss"),
            ("Imaginarium_Theater/Seasons", "Imaginarium Theater"),
            ("Stygian_Onslaught", "Stygian Onslaught"),
        ],
        default_start_time="10:00:00",
        banner_keyword="event wish"
    )


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
                    img_url = extract_img_from_html_node(cols[0]) or extract_img_from_html_node(row)

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
                                "imageUrl": img_url
                            }
                            if linked_page:
                                linked_meta[linked_page] = {
                                    "table_title": title_text,
                                    "fallback_id": fallback_id,
                                    "type_hint": type_text,
                                    "default_type": ev_type,
                                    "default_subtype": sub_type,
                                    "html_image": img_url
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
                            "default_subtype": subtype_name,
                            "html_image": None
                        }
        except Exception as e:
            print(f"[ZZZ] Error leyendo {channel_page}: {e}")

    if linked_meta:
        wikitexts = fetch_wikitext_batch(api_url, list(linked_meta.keys()))
        page_images = resolve_images_for_pages(api_url, wikitexts)

        for page_title, wt in wikitexts.items():
            meta = linked_meta.get(page_title, {})
            img = page_images.get(page_title) or meta.get("html_image")
            parsed = parse_infobox_event(
                game_id="zzz",
                api_url=api_url,
                page_title=page_title,
                wikitext=wt,
                version_cache=version_cache,
                override_display_title=meta.get("table_title"),
                type_hint=meta.get("type_hint", ""),
                default_type=meta.get("default_type", "EVENTO"),
                default_subtype=meta.get("default_subtype", "In-Game Event"),
                image_url=img
            )
            if parsed:
                fb_id = meta.get("fallback_id")
                if fb_id and fb_id in events_by_id:
                    del events_by_id[fb_id]
                events_by_id[parsed["id"]] = parsed

    result = list(events_by_id.values())
    zzz_season_img = next((ev["imageUrl"] for ev in result if ev.get("type") == "BANNER" and ev.get("imageUrl")), None)
    if zzz_season_img:
        for ev in result:
            if not ev.get("imageUrl"):
                ev["imageUrl"] = zzz_season_img

    print(f"[ZZZ] Eventos capturados: {len(result)}")
    return result


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
        "games": GAMES_CATALOG,
        "events": sorted_events
    }

    with open("events.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Total final en events.json: {len(sorted_events)} eventos.")


if __name__ == "__main__":
    main()
