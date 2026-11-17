"""Import a single job from a link I paste (LinkedIn, Indeed, Workday, Reddit, anything).

Only Greenhouse and Lever links are fetched, through their official public APIs.
Every other site is never contacted: I supply the company and title myself.
A Reddit post I found the job in is recorded as provenance, without contacting Reddit.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.pipeline import ingest
from app.ingestion.urls import canonicalize_url
from app.models import Company, IngestionRun, JobSource
from app.sources.base import RawJob, Source
from app.sources.greenhouse import GreenhouseSource
from app.sources.http import AccessPolicy, SourceError
from app.sources.lever import LeverSource

_GH_PATH = re.compile(r"^/([A-Za-z0-9_-]+)/jobs/(\d+)$")
_LEVER_PATH = re.compile(r"^/([A-Za-z0-9_.-]+)/([0-9a-fA-F-]{8,64})$")
_REDDIT_POST = re.compile(r"^(?:www\.|old\.|new\.)?reddit\.com$|^redd\.it$")
_REDDIT_ID = re.compile(r"/comments/([a-z0-9]+)")
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")

# Hosts that are never job applications (for picking links out of pasted post text).
_NOT_JOB_HOSTS = ("reddit.com", "redd.it", "imgur.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
                  "discord.gg", "discord.com", "google.com/search")
# Applicant tracking systems first, then generic career pages, then job boards.
_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "icims.com", "smartrecruiters.com",
              "jobvite.com", "workable.com", "taleo.net", "successfactors.com", "bamboohr.com", "eightfold.ai")
_BOARD_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "handshake")


class LinkImportError(ValueError):
    pass


@dataclass
class LinkImport:
    url: str
    company: str | None = None
    title: str | None = None
    location: str | None = None
    description: str | None = None
    found_via: str | None = None  # e.g. the Reddit post where I saw it


@dataclass
class LinkImportResult:
    job_id: int
    created: bool
    fetched_from: str | None  # "greenhouse" / "lever" when details came from an official API
    run: IngestionRun


class _ListSource(Source):
    """Feeds already-built RawJobs through the normal pipeline."""

    policy = AccessPolicy(name="manual", min_interval_s=0)
    authoritative = False

    def __init__(self, kind: str, jobs: list[RawJob]):
        self.kind = kind
        self.jobs = jobs

    def fetch(self, ref, company_name=None):
        return self.jobs


def extract_job_links(text: str) -> list[str]:
    """URLs in pasted text that look like job postings, best candidates first."""
    seen: dict[str, str] = {}
    for url in _URL_IN_TEXT.findall(text):
        url = url.rstrip(".,;:!?")
        lowered = url.lower()
        if any(h in lowered for h in _NOT_JOB_HOSTS):
            continue
        seen.setdefault(canonicalize_url(url), url)

    def rank(url: str) -> int:
        host = urlsplit(url).netloc.lower()
        if any(h in host for h in _ATS_HOSTS):
            return 0
        if any(h in host for h in _BOARD_HOSTS):
            return 2
        return 1

    return sorted(seen.values(), key=rank)


def _validate_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise LinkImportError("link must be a full http(s) URL")
    return url


def _reddit_provenance(found_via: str | None) -> str | None:
    if not found_via:
        return None
    parts = urlsplit(found_via.strip())
    if not _REDDIT_POST.match(parts.netloc.lower()):
        return None
    m = _REDDIT_ID.search(parts.path)
    if m:
        return m.group(1)
    return parts.path.strip("/") if parts.netloc.lower() == "redd.it" else None


def _known_company(session: Session, ats: str, token: str) -> str | None:
    return session.scalar(select(Company.name).where(Company.ats_type == ats, Company.ats_token == token))


def _fetch_from_ats(session: Session, url: str, company: str | None,
                    greenhouse: GreenhouseSource, lever: LeverSource) -> RawJob | None:
    canonical = canonicalize_url(url)
    parts = urlsplit(canonical)
    if parts.netloc == "boards.greenhouse.io" and (m := _GH_PATH.match(parts.path)):
        board, job_id = m.group(1), m.group(2)
        return greenhouse.fetch_one(board, job_id, company or _known_company(session, "greenhouse", board))
    if parts.netloc == "jobs.lever.co" and (m := _LEVER_PATH.match(parts.path)):
        token, posting_id = m.group(1), m.group(2)
        return lever.fetch_one(token, posting_id, company or _known_company(session, "lever", token) or token)
    # Company career pages that embed Greenhouse: ?gh_jid=123 plus a company I already track.
    gh_jid = parse_qs(parts.query).get("gh_jid", [None])[0]
    if gh_jid and gh_jid.isdigit() and company:
        board = session.scalar(
            select(Company.ats_token).where(Company.ats_type == "greenhouse", Company.name.ilike(company))
        )
        if board:
            return replace(greenhouse.fetch_one(board, gh_jid, company), apply_url=url)
    return None


def import_link(
    session: Session,
    req: LinkImport,
    greenhouse: GreenhouseSource | None = None,
    lever: LeverSource | None = None,
) -> LinkImportResult:
    url = _validate_url(req.url)
    reddit_id = _reddit_provenance(req.found_via)
    kind = "reddit" if reddit_id else "manual"
    url_hash = hashlib.sha1(canonicalize_url(url).encode()).hexdigest()[:16]
    external_id = f"{reddit_id}:{url_hash}" if reddit_id else url_hash

    try:
        fetched = _fetch_from_ats(session, url, req.company, greenhouse or GreenhouseSource(), lever or LeverSource())
    except SourceError as exc:
        raise LinkImportError(f"couldn't load that posting from the employer's job board: {exc}") from exc

    if fetched is None:
        if not (req.company and req.title):
            raise LinkImportError(
                "this site isn't fetched automatically (only Greenhouse and Lever are); "
                "add the company and job title"
            )
        base = RawJob(
            source_kind=kind, external_id=external_id, company_name=req.company, title=req.title,
            apply_url=url, location_raw=req.location, description_text=req.description,
        )
        raws = [base]
    else:
        if req.description and not (fetched.description_text or fetched.description_html):
            fetched.description_text = req.description
        # The ATS record plus my own provenance record; the pipeline merges them into one job.
        raws = [fetched, replace(fetched, source_kind=kind, external_id=external_id,
                                 source_url=req.found_via or None, raw={"pasted_url": url})]

    existing = session.scalar(select(JobSource.job_id).where(
        JobSource.source_kind == raws[0].source_kind, JobSource.external_id == raws[0].external_id))
    run = ingest(session, _ListSource(kind, raws), reddit_id or "pasted")
    if run.errors:
        raise LinkImportError(f"import failed: {run.errors[0]['error']}")
    job_id = session.scalar(select(JobSource.job_id).where(
        JobSource.source_kind == raws[0].source_kind, JobSource.external_id == raws[0].external_id))
    return LinkImportResult(job_id=job_id, created=existing is None and run.new_count > 0,
                            fetched_from=fetched.source_kind if fetched else None, run=run)
