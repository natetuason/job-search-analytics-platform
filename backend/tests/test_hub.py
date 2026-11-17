from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.hub import indeed_search, linkedin_search, load_hub

REPO_HUB = Path(__file__).resolve().parents[2] / "config" / "apply_hub.yaml"


def test_search_links_are_prefiltered() -> None:
    q = parse_qs(urlparse(linkedin_search("Data Analyst", "New Jersey, United States")).query)
    assert q == {"keywords": ["Data Analyst"], "location": ["New Jersey, United States"], "f_E": ["2,3"],
                 "f_JT": ["F"], "f_TPR": ["r604800"], "sortBy": ["DD"]}
    q = parse_qs(urlparse(indeed_search("Data Analyst", "New York City Metropolitan Area")).query)
    assert q["l"] == ["New York, NY"] and q["explvl"] == ["entry_level"] and q["fromage"] == ["7"]


def test_repo_hub_loads() -> None:
    links = load_hub(REPO_HUB)
    groups = [link.group for link in links]
    assert groups == sorted(groups, key=["LinkedIn searches", "Indeed searches", "Job sites",
                                         "Employer career sites"].index)
    assert any(link.name == "Johnson & Johnson" and link.checked == "blocked" for link in links)
    assert all(link.url.startswith("https://") for link in links)


def test_hub_validates_entries(tmp_path: Path) -> None:
    p = tmp_path / "hub.yaml"
    p.write_text("employers:\n  - { name: X, url: 'http://x.com' }\n")
    with pytest.raises(ValueError, match="https"):
        load_hub(p)
    p.write_text("sites:\n  - { name: X }\n")
    with pytest.raises(ValueError, match="name and url"):
        load_hub(p)


def test_private_links_are_added_first(tmp_path) -> None:
    private = tmp_path / "apply_hub.yaml"
    private.write_text('sites:\n  - { name: My School Handshake, url: "https://myschool.joinhandshake.com/" }\n')
    links = load_hub(REPO_HUB, private)
    sites = [link.name for link in links if link.group == "Job sites"]
    assert sites[0] == "My School Handshake" and len(links) == len(load_hub(REPO_HUB)) + 1
    assert load_hub(REPO_HUB, tmp_path / "missing.yaml") == load_hub(REPO_HUB)
