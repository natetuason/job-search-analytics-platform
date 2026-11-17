"""Command line.

  python -m app.cli ingest all                       # boards + sheets + lists from config/
  python -m app.cli ingest boards                    # config/companies.yaml only
  python -m app.cli ingest sheets                    # config/sheets.yaml only
  python -m app.cli ingest lists                     # config/lists.yaml only (public GitHub job lists)
  python -m app.cli ingest adzuna [--max-calls N]    # config/adzuna.yaml (paid calls; capped)
  python -m app.cli ingest greenhouse <token> [--name N]
  python -m app.cli ingest lever <token> [--name N]
  python -m app.cli ingest ashby <board> [--name N]
  python -m app.cli ingest sheet <sheet url> [--name N]
  python -m app.cli import-csv <file.csv> [--name N] # a CSV I downloaded myself
  python -m app.cli add-link <url> [--company C --title T --location L --description-file F --via REDDIT_URL]
  python -m app.cli find-links < post.txt            # list job links in pasted text
  python -m app.cli hub                              # links I check by hand (config/apply_hub.yaml)
  python -m app.cli score                            # enrich + score all active jobs
  python -m app.cli top [--n 25] [--rec apply,maybe] [--sample]   # ranked list / tuning sample
"""

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import select

from app.backlog import write_backlog
from app.config import get_settings
from app.db import get_sessionmaker
from app.hub import load_hub
from app.ingestion.batch import (
    BOARD_SOURCES, BoardConfig, ListConfig, SheetConfig, load_adzuna, load_companies, load_lists, load_sheets,
    run_adzuna, run_boards, run_lists, run_sheets, summary_line,
)
from app.ingestion.manual import LinkImport, LinkImportError, extract_job_links, import_link
from app.ingestion.pipeline import ingest
from app.log import configure_logging
from app.models import Company, Job, JobScore
from app.scoring.rules import SCORER
from app.scoring.run import score_all
from app.sources.csv_file import CsvFileSource

def cmd_ingest(args, parser) -> int:
    settings = get_settings()
    configure_logging("WARNING" if args.source in ("all", "boards", "sheets", "lists", "adzuna") else settings.log_level)
    if args.source == "adzuna":  # paid API: never part of "all"; run explicitly
        with get_sessionmaker()() as session:
            failures = run_adzuna(session, load_adzuna(settings.config_dir / "adzuna.yaml"), args.max_calls)
        return 1 if failures else 0
    boards: list[BoardConfig] = []
    sheets: list[SheetConfig] = []
    lists: list[ListConfig] = []
    if args.source in ("all", "boards"):
        boards = load_companies(settings.config_dir / "companies.yaml")
    if args.source in ("all", "sheets"):
        sheets = load_sheets(settings.config_dir / "sheets.yaml")
    if args.source in ("all", "lists"):
        lists = load_lists(settings.config_dir / "lists.yaml")
    if args.source in BOARD_SOURCES or args.source == "sheet":
        if not args.ref:
            parser.error(f"'{args.source}' needs a token or URL")
        if args.source == "sheet":
            sheets = [SheetConfig(args.name or args.ref, args.ref)]
        else:
            boards = [BoardConfig(args.name or args.ref, args.source, args.ref)]

    with get_sessionmaker()() as session:
        failures = run_boards(session, boards) + run_sheets(session, sheets) + run_lists(session, lists)
    total = len(boards) + len(sheets) + len(lists)
    print(f"\n{total - failures}/{total} sources succeeded")
    return 1 if total and failures == total else 0


def csv_label(path: Path) -> str:
    """'swejoblists-2026-11-05.csv' -> 'swejoblists', so re-downloads map to the same records."""
    return re.sub(r"[-_ ]?\d{4}-\d{2}-\d{2}$", "", path.stem) or path.stem


def cmd_import_csv(args) -> int:
    configure_logging("WARNING")
    path = Path(args.file)
    source = CsvFileSource(args.name or csv_label(path))
    with get_sessionmaker()() as session:
        run = ingest(session, source, str(path.resolve()))
    line = summary_line(run, f"csv_file:{source.label}")
    if source.skipped_rows:
        line += f"  skipped_no_link={len(source.skipped_rows)}"
    print(line)
    return 1 if run.status == "failed" else 0


def cmd_add_link(args) -> int:
    configure_logging("WARNING")
    description = Path(args.description_file).read_text() if args.description_file else None
    req = LinkImport(url=args.url, company=args.company, title=args.title, location=args.location,
                     description=description, found_via=args.via)
    with get_sessionmaker()() as session:
        try:
            result = import_link(session, req)
        except LinkImportError as exc:
            print(f"not imported: {exc}", file=sys.stderr)
            return 1
    how = f"details loaded from {result.fetched_from}" if result.fetched_from else "details as entered"
    print(f"{'added' if result.created else 'already tracked'}: job #{result.job_id} ({how})")
    return 0


def cmd_find_links(_args) -> int:
    links = extract_job_links(sys.stdin.read())
    print("\n".join(links) if links else "no job links found")
    return 0 if links else 1


def cmd_hub(_args) -> int:
    group = None
    for link in load_hub(get_settings().config_dir / "apply_hub.yaml", get_settings().private_dir / "apply_hub.yaml"):
        if link.group != group:
            group = link.group
            print(f"\n== {group}")
        extra = "  (blocks automated checks; open in browser)" if link.checked == "blocked" else ""
        print(f"  {link.name}{extra}\n    {link.url}" + (f"\n    {link.note}" if link.note else ""))
    return 0


def cmd_backlog(_args) -> int:
    configure_logging("WARNING")
    settings = get_settings()
    with get_sessionmaker()() as session:
        n = write_backlog(session, settings.config_dir, settings.private_dir, settings.backlog_dir)
    print(f"wrote {n} applications to {settings.backlog_dir}/ (applications.md, applications.csv, rollup.md)")
    return 0


def cmd_score(_args) -> int:
    configure_logging("WARNING")
    settings = get_settings()
    with get_sessionmaker()() as session:
        summary = score_all(session, settings.config_dir, settings.private_dir / "profile.yaml")
    recs = ", ".join(f"{k}={v}" for k, v in sorted(summary.by_recommendation.items()))
    print(f"scored {summary.scored} active jobs ({recs}); pay read from descriptions: {summary.pay_parsed}; "
          f"pay estimates: {summary.estimated}")
    return 0


def _print_scored(rows) -> None:
    for score, job, company in rows:
        ex = score.explanation
        flags = "; ".join(f"{f['label']} ({f['status'].replace('_', ' ')})" for f in score.hard_flags)
        cats = score.breakdown["categories"]
        print(f"{float(score.score):5.1f}  {score.recommendation.upper():5}  {score.tier:11}  {job.title}  @ {company.name}")
        print(f"       {ex['location']}  |  {ex['compensation']}")
        print(f"       skills {cats['skills']:.0f}  exp {cats['experience']:.0f}  resp {cats['responsibilities']:.0f}  "
              f"pay {cats['compensation']:.0f}  loc {cats['location']:.0f}"
              + ("  +rotational" if score.breakdown.get("rotational_bonus") else "")
              + f"  |  {ex['experience_fit']}")
        if ex["skills"].get("missing"):
            print(f"       missing: {', '.join(ex['skills']['missing'][:6])}")
        if flags:
            print(f"       flags: {flags}")
        print(f"       {' / '.join(ex['reasons'])}")
        print(f"       {job.apply_url}")


def cmd_top(args) -> int:
    recs = [r.strip() for r in args.rec.split(",")]
    base = (select(JobScore, Job, Company).join(Job, Job.id == JobScore.job_id).join(Company, Company.id == Job.company_id)
            .where(JobScore.scorer == SCORER, Job.is_active.is_(True)))
    with get_sessionmaker()() as session:
        if not args.sample:
            rows = session.execute(base.where(JobScore.recommendation.in_(recs))
                                   .order_by(JobScore.score.desc(), Job.posted_at.desc().nulls_last()).limit(args.n)).all()
            _print_scored(rows)
            return 0
        sections = [
            ("Top Apply", base.where(JobScore.recommendation == "apply").order_by(JobScore.score.desc()).limit(10)),
            ("Just above the Apply line", base.where(JobScore.recommendation == "apply").order_by(JobScore.score.asc()).limit(5)),
            ("Top Maybe", base.where(JobScore.recommendation == "maybe").order_by(JobScore.score.desc()).limit(5)),
            ("Highest-scoring Skips", base.where(JobScore.recommendation == "skip").order_by(JobScore.score.desc()).limit(5)),
        ]
        for label, stmt in sections:
            print(f"\n===== {label}")
            _print_scored(session.execute(stmt).all())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="fetch jobs from configured or given sources")
    p_ingest.add_argument("source", choices=["all", "boards", "sheets", "lists", "adzuna", *BOARD_SOURCES, "sheet"])
    p_ingest.add_argument("ref", nargs="?", help="board token or sheet URL")
    p_ingest.add_argument("--name", help="display name")
    p_ingest.add_argument("--max-calls", type=int, help="adzuna: at most this many API calls this run")

    p_csv = sub.add_parser("import-csv", help="import a job-list CSV file I downloaded")
    p_csv.add_argument("file")
    p_csv.add_argument("--name", help="label for this list (default: file name without date)")

    p_add = sub.add_parser("add-link", help="track one job from a pasted link")
    p_add.add_argument("url")
    p_add.add_argument("--company")
    p_add.add_argument("--title")
    p_add.add_argument("--location")
    p_add.add_argument("--description-file", help="text file with the job description")
    p_add.add_argument("--via", help="where I found it, e.g. a Reddit post URL")

    sub.add_parser("find-links", help="print job links found in text on stdin")
    sub.add_parser("hub", help="print the apply hub: sites I check by hand")
    sub.add_parser("score", help="enrich and score all active jobs")
    sub.add_parser("backlog", help="regenerate backlog/ (applications.md, .csv, rollup.md) from applied jobs")
    p_top = sub.add_parser("top", help="print the ranked list")
    p_top.add_argument("--n", type=int, default=25)
    p_top.add_argument("--rec", default="apply,maybe", help="comma list of apply, maybe, skip")
    p_top.add_argument("--sample", action="store_true", help="a sample across score bands, for tuning")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        return cmd_ingest(args, parser)
    if args.command == "import-csv":
        return cmd_import_csv(args)
    if args.command == "add-link":
        return cmd_add_link(args)
    if args.command == "hub":
        return cmd_hub(args)
    if args.command == "score":
        return cmd_score(args)
    if args.command == "backlog":
        return cmd_backlog(args)
    if args.command == "top":
        return cmd_top(args)
    return cmd_find_links(args)


if __name__ == "__main__":
    sys.exit(main())
