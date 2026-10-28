"""Add one job you found yourself: score it, and if it passes, tailor the resume and prepare the extension.

    python -m apply_agent.add --url <apply-url> --jd jd.txt [--company X] [--title Y] [--role swe|pm]
                              [--min-score 65] [--force] [--no-tailor]
    pbpaste | python -m apply_agent.add --url <apply-url>      # JD on stdin

Skips discovery, ranking and the jobs_per_day cap. Everything after scoring is the normal pipeline code.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, Field

from apply_agent.core import llm, summary
from apply_agent.core.config import DATA, ROOT, load_config, run_dir as default_run_dir
from apply_agent.core.models import Job, read_jobs, write_jobs
from apply_agent.core.state import SeenJobs, fingerprint
from apply_agent.prepare import run as preparer
from apply_agent.score.run import score
from apply_agent.tailor import run as optimizer
from apply_agent.tailor.inbox import Inbox
from apply_agent.tailor.customize import run_all

EXTRACT_SYSTEM = ("You read one job posting and extract its basic facts. Use only what the text says; "
                  "if something is not stated, say 'unknown' or leave the list empty. Return exactly these keys: "
                  "company, job_title (the role's title), role_type, locations, sponsorship_signal, years_required_min.")


class Meta(BaseModel):
    company: str = Field(description="Hiring company name")
    job_title: str = Field(default="", description="The role's title, e.g. 'Software Engineer, University Graduate'")
    role_type: Literal["SWE", "PM"] = Field(description="SWE for engineering roles, PM for product management roles")
    locations: list[str] = Field(description="Locations named in the posting, empty if none")
    sponsorship_signal: Literal["yes", "no", "unknown"] = Field(description="Does the posting offer visa sponsorship?")
    years_required_min: int | None = Field(description="Minimum years of experience required, null if not stated")


def build_job(url: str, jd: str, args, cfg) -> Job:
    meta = asyncio.run(llm.structured(cfg.matchmaker.model, EXTRACT_SYSTEM, jd[:12000], Meta,
                                      effort="low", max_tokens=1500))
    company = args.company or meta.company
    title = args.title or meta.job_title
    if not title:
        sys.exit("couldn't read the job title from the JD; pass --title")
    role = (args.role or meta.role_type).upper()
    if role not in ("SWE", "PM"):
        sys.exit(f"role must be swe or pm (got {role!r}); pass --role")
    sig = meta.sponsorship_signal if meta.sponsorship_signal in ("yes", "no") else "unknown"
    return Job(id=f"manual:{hashlib.sha1(url.encode()).hexdigest()[:10]}", source="manual", company=company,
               title=title, role_type=role, locations=meta.locations, url=url, description=jd,
               sponsorship_signal=sig, years_required_min=meta.years_required_min,
               fingerprint=fingerprint(company, title))


def tailor_and_prepare(job: Job, cfg, rd: Path) -> Job:
    job = asyncio.run(run_all([job], cfg, rd))[0]
    optimizer._record([job], rd, cfg)  # questions inbox, seen_jobs, usage
    top = rd / "03_top.json"
    prev = read_jobs(top)
    job.rank = max([j.rank or 0 for j in prev], default=0) + 1
    write_jobs(top, [j for j in prev if j.id != job.id] + [job])  # shows up in today's SUMMARY
    if job.customize_status in ("ok", "needs_answers"):
        preparer.main(rd, cfg, no_sheet=True)  # answers.json, fill_by_hand.json, extension packets
    summary.write(rd, spent=llm.USAGE.usd, n_open_questions=len(Inbox().open), closed=[], failed_step=None)
    return job


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="the apply-page URL (the extension matches on it)")
    ap.add_argument("--jd", type=Path, help="file with the job description (default: stdin)")
    ap.add_argument("--company")
    ap.add_argument("--title")
    ap.add_argument("--role", help="swe | pm (default: inferred from the JD)")
    ap.add_argument("--min-score", type=int)
    ap.add_argument("--force", action="store_true", help="tailor even if it fails the score / hard checks")
    ap.add_argument("--no-tailor", action="store_true", help="score only")
    a = ap.parse_args()
    cfg = load_config()
    if (DATA / "run.lock").exists():
        print("✗ a pipeline run is in progress; add the job after it finishes")
        return 1
    jd = (a.jd.read_text(encoding="utf-8") if a.jd else sys.stdin.read()).strip()
    if len(jd) < 200:
        print("✗ the job description looks empty or too short")
        return 1
    rd = default_run_dir(cfg)
    min_score = a.min_score if a.min_score is not None else cfg.matchmaker.min_score
    llm.USAGE.budget_usd = cfg.daily_budget_usd

    job = build_job(a.url, jd, a, cfg)
    seen = SeenJobs()
    prior = seen.get(job.id)
    if prior and prior.get("status") in ("customized", "applied") and not a.force:
        print(f"✗ already {prior['status']}: {job.company} · {job.title} ({prior.get('resume_dir', '')})")
        return 1
    dupe = seen._fp_recent(job.fingerprint, {"customized", "applied"})
    if dupe and not a.force:
        print(f"✗ looks like a job you already have ({dupe.get('company')} · {dupe.get('title')}, "
              f"{dupe.get('status')}); use --force to add anyway")
        return 1

    job = asyncio.run(score(job, cfg))
    ok = bool(job.meets_needs) and (job.match_score or 0) >= min_score
    print(f"\n{job.company} · {job.title} [{job.role_type}] · match {job.match_score} (min {min_score})")
    print(f"  hard checks: {'pass' if job.meets_needs else 'FAIL'} — {job.needs_reasons}")
    for label, items in (("aligned", job.alignment), ("gaps", job.gaps)):
        for it in items:
            print(f"  {label}: {it}")

    if not ok and not a.force:
        seen.set(job.id, "rejected", fingerprint=job.fingerprint, match_score=job.match_score,
                 company=job.company, title=job.title, url=job.url)
        seen.save()
        print("\n✗ below the bar; not tailored (re-run with --force to tailor anyway)")
        return 0
    if a.no_tailor:
        return 0

    print("\n… tailoring (a few minutes)")
    job = tailor_and_prepare(job, cfg, rd)
    print(f"\n✓ {job.customize_status}: {job.resume_pdf}")
    if job.open_questions:
        print(f"  open questions: {', '.join(job.open_questions)} (see resume/questions.md)")
    for w in job.warnings:
        print(f"  ⚠ {w}")
    print(f"  mark applied later: python -m apply_agent.prepare.applied {Path(job.resume_dir).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
