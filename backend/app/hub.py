"""Apply hub: links to places the app doesn't fetch, so I check them by hand.

LinkedIn and Indeed search links are only built here; the app never requests them.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

import yaml

# LinkedIn: f_E 1=Internship 2=Entry level 3=Associate; we use entry + associate.
# f_TPR=r604800 is "past week"; sortBy=DD is newest first.
_LINKEDIN = "https://www.linkedin.com/jobs/search/"
_INDEED = "https://www.indeed.com/jobs"
_INDEED_LOCATIONS = {"New York City Metropolitan Area": "New York, NY", "New Jersey, United States": "New Jersey"}


@dataclass
class HubLink:
    group: str
    name: str
    url: str
    note: str | None = None
    checked: str | None = None


def linkedin_search(keywords: str, location: str) -> str:
    return f"{_LINKEDIN}?" + urlencode({"keywords": keywords, "location": location,
                                         "f_E": "2,3", "f_JT": "F", "f_TPR": "r604800", "sortBy": "DD"})


def indeed_search(keywords: str, location: str) -> str:
    return f"{_INDEED}?" + urlencode({"q": keywords, "l": _INDEED_LOCATIONS.get(location, location),
                                       "explvl": "entry_level", "jt": "fulltime", "fromage": "7", "sort": "date"})


def load_hub(path: Path, private: Path | None = None) -> list[HubLink]:
    """Links from config/apply_hub.yaml plus, if it exists, my own additions in data/private/apply_hub.yaml
    (school portals and other personal links that shouldn't be in the shared repo)."""
    data = yaml.safe_load(path.read_text()) or {}
    if private is not None and private.exists():
        extra = yaml.safe_load(private.read_text()) or {}
        for key in ("sites", "employers"):
            data[key] = list(extra.get(key) or []) + list(data.get(key) or [])
    links: list[HubLink] = []
    searches = data.get("searches") or {}
    for kw in searches.get("keywords") or []:
        for loc in searches.get("locations") or []:
            links.append(HubLink("LinkedIn searches", f"{kw} — {loc}", linkedin_search(kw, loc)))
            links.append(HubLink("Indeed searches", f"{kw} — {loc}", indeed_search(kw, loc)))
    for group, key in (("Job sites", "sites"), ("Employer career sites", "employers")):
        for i, entry in enumerate(data.get(key) or []):
            if not entry or not entry.get("name") or not entry.get("url"):
                raise ValueError(f"{path.name} {key}[{i}]: needs name and url")
            if urlparse(entry["url"]).scheme != "https":
                raise ValueError(f"{path.name} {key}[{i}]: url must be https")
            links.append(HubLink(group, str(entry["name"]), entry["url"], entry.get("note"),
                                 str(entry["checked"]) if entry.get("checked") else None))
    links.sort(key=lambda link: ["LinkedIn searches", "Indeed searches", "Job sites",
                                 "Employer career sites"].index(link.group))
    return links
