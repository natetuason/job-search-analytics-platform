"""Pure functions that turn source text into consistent, comparable values."""

import hashlib
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
STATE_CODES = set(US_STATES.values())
US_NAMES = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"}

_COMPANY_SUFFIXES = {
    "incorporated", "inc", "llc", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "lp", "llp", "gmbh", "sa", "ag", "nv", "bv",
}
_TITLE_ABBREVIATIONS = {
    r"\bsr\b": "senior",
    r"\bjr\b": "junior",
    r"\bmgr\b": "manager",
    r"\bassoc\b": "associate",
    r"\bassc\b": "associate",
    r"\bops\b": "operations",
    r"\bbiz\b": "business",
    r"\banlst\b": "analyst",
}


def _fold(text: str) -> str:
    """Lowercase, strip accents, turn punctuation into spaces, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_company(name: str) -> str:
    folded = _fold(name)
    # Join runs of single letters ("s a" from "S.A.", "l l c") so they read as one word.
    folded = re.sub(r"\b(?:[a-z] )+[a-z]\b", lambda m: m.group().replace(" ", ""), folded)
    tokens = folded.split()
    if tokens and tokens[0] == "the" and len(tokens) > 1:
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_title(title: str) -> str:
    text = _fold(title)
    for pattern, full in _TITLE_ABBREVIATIONS.items():
        text = re.sub(pattern, full, text)
    return re.sub(r"\s+", " ", text).strip()


# ---------- HTML ----------

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    parser = _TextExtractor()
    parser.feed(unescape(raw_html) if "&lt;" in raw_html else raw_html)
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------- Location ----------

def _parse_one_location(piece: str, country_hint: str | None) -> dict:
    loc: dict = {"city": None, "state": None, "country": None, "remote": False}
    text = piece.strip()
    if re.search(r"\bremote\b", text, re.I):
        loc["remote"] = True
        text = re.sub(r"\bremote\b\s*[-–:,(]?\s*", "", text, flags=re.I).strip(" -–,()")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    lowered = [p.lower() for p in parts]
    if lowered and lowered[-1] in US_NAMES:
        loc["country"] = "US"
        parts, lowered = parts[:-1], lowered[:-1]
    for i in range(len(parts) - 1, -1, -1):
        word = parts[i]
        code = word.upper() if word.upper() in STATE_CODES and len(word) == 2 else US_STATES.get(lowered[i])
        if code:
            loc["state"] = code
            loc["country"] = "US"
            parts = parts[:i]
            break
    if parts:
        if len(parts) == 1 and parts[0].lower() in US_NAMES:
            loc["country"] = "US"
        elif loc["state"] or len(parts) == 1:
            loc["city"] = parts[0]
        else:
            loc["city"], loc["country"] = parts[0], parts[-1]
    if loc["country"] is None and country_hint:
        loc["country"] = "US" if country_hint.upper() in ("US", "USA") else country_hint.upper()
    return loc


def parse_locations(raw: str | None, country_hint: str | None = None) -> list[dict]:
    if not raw or not raw.strip():
        return []
    pieces = re.split(r"\s*(?:;|\||/| or |\n)\s*", raw)
    return [_parse_one_location(p, country_hint) for p in pieces if p.strip()]


def location_key(locations: list[dict], raw: str | None) -> str:
    """Stable string for de-duplication, using the first parsed location."""
    if locations:
        first = locations[0]
        bits = [first.get("city") or "", first.get("state") or "", first.get("country") or ""]
        key = _fold(" ".join(bits))
        if first.get("remote"):
            key = ("remote " + key).strip()
        if key:
            return key
    return _fold(raw or "")


# ---------- Work arrangement / employment type ----------

_HINTS = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite", "in-office": "onsite"}


def detect_work_arrangement(hint: str | None, location_raw: str | None, title: str, description: str) -> str:
    if hint and hint.lower() in _HINTS:
        return _HINTS[hint.lower()]
    head = f"{title} {location_raw or ''}".lower()
    if "hybrid" in head:
        return "hybrid"
    if re.search(r"\bremote\b", head):
        return "remote"
    if re.search(r"\b(on-?site|in[- ]office)\b", head):
        return "onsite"
    body = description.lower()
    if re.search(r"\bhybrid\b", body):
        return "hybrid"
    if re.search(r"\b(fully|100%) remote\b|\bremote[- ]first\b|\bthis (role|position) is remote\b", body):
        return "remote"
    return "unknown"


_EMPLOYMENT = [
    (r"\b(intern|internship|co-?op|summer analyst)\b", "internship"),
    (r"\bpart[- ]time\b", "part_time"),
    (r"\b(contract|contractor|temporary|temp|fixed[- ]term)\b", "contract"),
    (r"\bfull[- ]time\b", "full_time"),
]


def detect_employment_type(hint: str | None, title: str) -> str:
    for text in (hint or "", title):
        for pattern, kind in _EMPLOYMENT:
            if re.search(pattern, text, re.I):
                return kind
    return "unknown"


# ---------- De-duplication ----------

def dedup_key(company: str, title: str, location: str) -> str:
    """sha256 of normalized company + title + location."""
    basis = f"{normalize_company(company)}|{normalize_title(title)}|{location}"
    return hashlib.sha256(basis.encode()).hexdigest()
