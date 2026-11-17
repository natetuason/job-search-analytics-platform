"""Write a shareable copy of this repo: committed files only, fresh git history, personal data checked for.

  python3 scripts/export_shareable.py ~/job-platform-shareable

- Copies the last commit (`git archive HEAD`), so uncommitted work, .env, data/private/, backlog/, analysis/ and ResumeProject.md are never included.
- Fails, and deletes the copy, if any file contains a term from data/private/share_denylist.txt
  (names, school, résumé details; that list itself stays private) or this machine's git email.
- The copy gets one fresh commit by a neutral author, so none of this repo's history goes with it.
- Never pushes anywhere. Create the GitHub repo and push yourself.
"""

import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DENYLIST = REPO / "data" / "private" / "share_denylist.txt"
FORBIDDEN_PATHS = (".env", "data/private/", "backlog/", "analysis/", "ResumeProject.md")
AUTHOR = ["-c", "user.name=Job Platform", "-c", "user.email=job-platform@users.noreply.github.com"]


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def denied_terms() -> list[str]:
    if not DENYLIST.exists():
        sys.exit(f"missing {DENYLIST.relative_to(REPO)}: list the personal terms to check for, one per line")
    terms = [t.strip() for t in DENYLIST.read_text().splitlines() if t.strip() and not t.startswith("#")]
    email = subprocess.run(["git", "config", "user.email"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    return terms + ([email] if email else [])


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dest = Path(sys.argv[1]).expanduser().resolve()
    if dest == REPO or REPO in dest.parents:
        sys.exit("destination must be outside this repo")
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} exists and isn't empty; remove it or pick another folder")
    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("note: uncommitted changes are NOT included (only the last commit is exported)")

    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=REPO, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        names = tar.getnames()
        bad = [n for n in names if n == ".env" or any(n.startswith(p) for p in FORBIDDEN_PATHS[1:])]
        if bad:
            sys.exit(f"refusing: private paths are committed: {bad}")
        dest.mkdir(parents=True, exist_ok=True)
        tar.extractall(dest, filter="data")

    terms = [t.lower() for t in denied_terms()]
    hits = []
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue  # binary (icons)
        hits += [f"{path.relative_to(dest)}: contains a private term" for t in terms if t in text]
    if hits:
        shutil.rmtree(dest)
        print("refusing: personal data found (copy deleted). Fix these in the repo and commit:")
        print("\n".join(sorted(set(hits))))
        return 1

    git("init", "-q", "-b", "main", cwd=dest)
    git("add", "-A", cwd=dest)
    git(*AUTHOR, "commit", "-q", "-m", "Job search platform (shareable copy)", cwd=dest)
    print(f"wrote {dest} ({len(names)} files, 1 commit, checked {len(terms)} private terms: none found)")
    print("next: create a PRIVATE GitHub repo, then: cd", dest, "&& git remote add origin <url> && git push -u origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
