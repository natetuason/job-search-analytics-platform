"""Classify a job's locations against my location settings.

preferred  NYC (any borough), Jersey City, or anywhere in New Jersey (Settings: preferred_locations)
remote     U.S. remote
secondary  another big U.S. city from Settings: secondary_locations
other      anywhere else in the U.S. or unclear
foreign    outside the U.S.
"""

import re

RANK = {"preferred": 4, "remote": 3, "secondary": 2, "other": 1, "foreign": 0}
_NYC = {"new york", "new york city", "nyc", "manhattan", "brooklyn", "queens", "bronx", "the bronx", "staten island"}
_NOISE = re.compile(r"\b(hq|office|headquarters|metro(?:politan)? area|area|downtown)\b", re.I)


def _city(name: str | None) -> str:
    return _NOISE.sub("", name or "").strip(" -,").lower()


def _places(entries: list[str]) -> tuple[set[str], set[tuple[str, str]], dict[str, str]]:
    """Split 'NJ' (whole state) from 'Jersey City, NJ' (city, state); also city -> state for bare city names."""
    states: set[str] = set()
    cities: set[tuple[str, str]] = set()
    for entry in entries:
        parts = [p.strip() for p in entry.split(",")]
        if len(parts) == 1 and len(parts[0]) == 2:
            states.add(parts[0].upper())
        elif len(parts) >= 2:
            cities.add((parts[0].lower(), parts[1].upper()))
    return states, cities, {city: state for city, state in cities}


def classify(locations: list[dict], preferred: list[str], secondary: list[str]) -> str:
    """Best class among a job's locations (a job listing NYC and Austin counts as preferred)."""
    pref_states, pref_cities, _ = _places(preferred)
    _, sec_cities, sec_city_state = _places(secondary)
    best = "other" if not locations else "foreign"
    for loc in locations:
        country, state, city = loc.get("country"), loc.get("state"), _city(loc.get("city"))
        # "New York - New York City", "Remote - Jersey City": look at each piece.
        pieces = {p.strip() for p in re.split(r"\s+-\s+|\s*\(\s*|\s*\)\s*", city) if p.strip()}
        if pieces & _NYC and state in (None, "NY"):
            city = "new york"
        elif "new jersey" in pieces:
            state = "NJ"
        if country not in (None, "US"):
            cls = "foreign"
        elif state in pref_states or (city, state) in pref_cities or (city in _NYC and state in (None, "NY")):
            cls = "preferred"
        elif loc.get("remote") and not city:
            cls = "remote" if country in (None, "US") else "foreign"
        elif (city, state) in sec_cities or (state is None and city in sec_city_state):
            cls = "secondary"
        elif state or country == "US":
            cls = "other"
        else:
            cls = "other"  # unclear (e.g. a bare city name we don't know)
        if RANK[cls] > RANK[best]:
            best = cls
    return best


def all_excluded(locations: list[dict], excluded: list[str]) -> bool:
    """True when every one of a job's locations is a place in Settings: excluded_locations ('NJ' or 'City, ST')."""
    if not locations or not excluded:
        return False
    states, cities, city_state = _places(excluded)

    def hit(loc: dict) -> bool:
        city, state = _city(loc.get("city")), loc.get("state")
        return state in states or (city, state) in cities or (state is None and city in city_state)

    return all(hit(loc) for loc in locations)
