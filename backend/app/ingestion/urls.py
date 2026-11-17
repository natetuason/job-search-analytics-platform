"""Canonical form of application URLs, so the same posting matches across sources."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "gh_src", "source", "src", "ref", "referrer", "lever-source", "lever-origin", "lever-via",
    "trk", "trackingid", "gclid", "fbclid", "mc_cid", "mc_eid", "ccuid", "jobsource", "urlhash",
}
_HOST_ALIASES = {"job-boards.greenhouse.io": "boards.greenhouse.io"}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    host = _HOST_ALIASES.get(host, host)
    path = parts.path.rstrip("/") or "/"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]

    # Greenhouse embed form -> the posting's board URL.
    if host == "boards.greenhouse.io" and path == "/embed/job_app":
        q = dict(query)
        if "for" in q and "token" in q:
            return f"https://boards.greenhouse.io/{q['for'].lower()}/jobs/{q['token']}"

    # Lever "/apply" form and posting page are the same job.
    if host == "jobs.lever.co" and path.endswith("/apply"):
        path = path.removesuffix("/apply")

    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))
