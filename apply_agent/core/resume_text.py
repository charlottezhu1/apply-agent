"""Plain-text views of the base resumes and confirmed facts, for prompts."""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from .config import APPLICANT, ROOT

RESUME = ROOT / "resume"
CUSTOMIZE_MD = RESUME / ".claude" / "commands" / "customize.md"
CONFIRMED_FACTS = APPLICANT / "facts.md"    # the source of truth; learned answers are appended here
PREFERENCES = APPLICANT / "preferences.md"
# The tailoring sessions run inside resume/ and read their facts from there, so they get a generated copy.
FACTS_MIRROR = RESUME / "confirmed_facts.md"
MIRROR_HEADER = "<!-- GENERATED from applicant/facts.md by apply_agent. Edit that file, not this one. -->\n\n"


def pdf_text(pdf: Path) -> str:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True)
    return out.stdout


@lru_cache
def base_resume_text(tex_path: str) -> str:
    pdf = (ROOT / tex_path).with_suffix(".pdf")
    if not pdf.exists():
        raise FileNotFoundError(f"compile the base resume first: {pdf}")
    return pdf_text(pdf).strip()


@lru_cache
def confirmed_facts() -> str:
    """The 'Pre-approved edits' section of customize.md plus applicant/facts.md (learned answers)."""
    parts = []
    if CUSTOMIZE_MD.exists():
        md = CUSTOMIZE_MD.read_text(encoding="utf-8")
        m = re.search(r"^## Pre-approved edits.*?(?=^## Steps|\Z)", md, re.S | re.M)
        if m:
            parts.append(m.group(0).strip())
    if CONFIRMED_FACTS.exists():
        parts.append(CONFIRMED_FACTS.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


@lru_cache
def preferences() -> str:
    return PREFERENCES.read_text(encoding="utf-8").strip() if PREFERENCES.exists() else ""


def sync_facts_mirror() -> bool:
    """Refresh resume/confirmed_facts.md from applicant/facts.md. True if it changed."""
    if not CONFIRMED_FACTS.exists() or not RESUME.exists():
        return False
    want = MIRROR_HEADER + CONFIRMED_FACTS.read_text(encoding="utf-8")
    if FACTS_MIRROR.exists() and FACTS_MIRROR.read_text(encoding="utf-8") == want:
        return False
    FACTS_MIRROR.write_text(want, encoding="utf-8")
    return True
