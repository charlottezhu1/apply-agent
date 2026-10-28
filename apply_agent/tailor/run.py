"""Stage 3: rank matched jobs, take jobs_per_day, tailor a resume for each, merge questions into the inbox.

    python -m apply_agent.tailor.run --run-dir runs/2026-10-03 [--rank-only] [--jobs N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from apply_agent.core import llm
from apply_agent.core.config import ROOT, load_config, run_dir as default_run_dir
from apply_agent.core.models import Job, read_jobs, write_jobs
from apply_agent.core.resume_text import RESUME
from apply_agent.core.state import SeenJobs

from .customize import run_all
from .inbox import Inbox
from .rank import rank

CUSTOMIZED = RESUME / "customized"


def folder_key(resume_dir: str) -> str:
    """'resume/customized/2026-10-04/Stripe_SWE' → '2026-10-04/Stripe_SWE' (the inbox's Asked-by key)."""
    return str((ROOT / resume_dir).relative_to(CUSTOMIZED))


def _record(jobs: list[Job], rd: Path, cfg) -> None:
    """Merge questions into the inbox, update seen_jobs and usage logs."""
    inbox = Inbox()
    asked = {folder_key(j.resume_dir): j.extra.get("questions_raw", []) for j in jobs if j.resume_dir}
    ids = asyncio.run(inbox.add(asked, date.today().isoformat(), cfg.submission.model)) if any(asked.values()) else {}
    inbox.save()
    seen = SeenJobs()
    usage_path = rd / "customize_usage.json"
    usage = json.loads(usage_path.read_text()) if usage_path.exists() else []
    for j in jobs:
        j.open_questions = ids.get(folder_key(j.resume_dir), []) if j.resume_dir else []
        status = "failed" if j.customize_status == "failed" else "customized"
        seen.set(j.id, status, fingerprint=j.fingerprint, company=j.company, title=j.title, url=j.url,
                 match_score=j.match_score, resume_dir=j.resume_dir, role=j.role_type)
        usage.append({"date": date.today().isoformat(), "job_id": j.id, "status": j.customize_status,
                      "cost_usd": j.extra.get("customize_cost_usd", 0), "questions": len(j.open_questions)})
    seen.save()
    usage_path.write_text(json.dumps(usage, indent=2))


def retailor(folders: list[str], rd: Path, cfg) -> list[Job]:
    """Re-run tailoring for existing folders after their questions were answered (doesn't use jobs_per_day)."""
    jobs = []
    for name in folders:
        p = CUSTOMIZED / name / "job.json"
        if p.exists():
            jobs.append(Job.model_validate_json(p.read_text()))
    if not jobs:
        return []
    done = asyncio.run(run_all(jobs, cfg, rd))
    _record(done, rd, cfg)
    write_jobs(rd / "03_retailored.json", done)
    return done


def main(rd: Path, cfg=None, n: int | None = None, rank_only: bool = False, dry_run: bool = False) -> Path:
    cfg = cfg or load_config()
    seen = SeenJobs()
    top = rank(read_jobs(rd / "02_matched.json"), seen, cfg, n=n)
    out = rd / "03_top.json"
    if rank_only or dry_run:
        write_jobs(out, top)
        for j in top:
            print(f"  {j.rank:>2}. {j.rank_score:.3f}  {j.match_score:>3}  {j.company} · {j.title}")
        return out
    done = asyncio.run(run_all(top, cfg, rd)) if top else []
    _record(done, rd, cfg)
    # merge with earlier results of the same day (idempotent re-runs)
    prev = {j.id: j for j in read_jobs(out)}
    prev.update({j.id: j for j in done})
    write_jobs(out, sorted(prev.values(), key=lambda j: j.rank or 999))
    ok = sum(j.customize_status != "failed" for j in done)
    q = sum(len(j.open_questions) for j in done)
    print(f"[optimizer] tailored {ok}/{len(done)} · {q} questions · total spend ${llm.USAGE.usd:.2f}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--jobs", type=int)
    ap.add_argument("--rank-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    main(a.run_dir or default_run_dir(cfg), cfg, a.jobs, a.rank_only, a.dry_run)
