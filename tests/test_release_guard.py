"""Before sharing or pushing: nothing private is tracked, and shipped files contain no personal data.

The second test builds its denylist from YOUR real applicant/profile.yaml, so it needs no list to maintain and
skips itself on a machine without one (a friend's fork). PENDING lists the shipped files that still mention the
applicant, each with the reason; the test fails when a file is clean but still listed, so the list only shrinks.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from apply_agent.core import profile as P

ROOT = Path(__file__).resolve().parent.parent
REAL_PROFILE = ROOT / "applicant" / "profile.yaml"
PRIVATE = ("applicant/", "resume/", "runs/", "data/", "personal_notes/", ".env", ".claude/settings.local.json",
           "chrome_extension/generated/")

PENDING: dict[str, str] = {}   # shipped files allowed to mention the applicant, with the reason (none today)


def shipped_files() -> list[str]:
    """Everything git would commit: tracked, plus untracked files that are not ignored."""
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [f for f in out.splitlines() if (ROOT / f).is_file()]


def test_nothing_private_is_shipped():
    bad = [f for f in shipped_files() if f.startswith(PRIVATE) and not f.startswith("tests/")]
    assert bad == [], f"private files would be committed: {bad[:5]}"


def test_templates_and_fixtures_are_shipped():
    """The private-folder rules in .gitignore must not swallow templates/applicant or tests/fixtures/applicant."""
    shipped = set(shipped_files())
    for f in ("templates/applicant/profile.yaml", "templates/applicant/preferences.md", "templates/applicant/facts.md",
              "templates/applicant/README.md", "tests/fixtures/applicant/profile.yaml"):
        assert f in shipped, f"{f} is ignored by git"


def tokens(p: P.Profile) -> set[str]:
    n, c, a, e = p.name, p.contact, p.address, p.education
    toks = {n.first, n.last, n.legal_first, n.legal_last, n.preferred_first, c.email, c.phone, a.street,
            e.school, e.undergrad_school}
    for url in (c.linkedin, c.github, c.website):
        if url:
            toks.add(url.rstrip("/").rsplit("/", 1)[-1])   # the handle / slug
    return {t.lower() for t in toks if t and len(t) >= 5}


def test_no_personal_data_in_shipped_files():
    if not REAL_PROFILE.exists():
        pytest.skip("no real applicant/profile.yaml on this machine")
    toks = tokens(P.load_profile(REAL_PROFILE))
    leaks = {}
    for f in shipped_files():
        if f.startswith("tests/fixtures/"):
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        hit = sorted(t for t in toks if t in text) + (["<home path>"] if re.search(r"/users/[a-z0-9]+/", text) else [])
        if hit:
            leaks[f] = hit
    unexpected = {f: h for f, h in leaks.items() if f not in PENDING}
    assert not unexpected, f"personal data in shipped files (token kinds hidden): {sorted(unexpected)}"
    stale = [f for f in PENDING if f not in leaks]
    assert not stale, f"clean now, remove from PENDING: {stale}"
