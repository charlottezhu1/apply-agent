"""Mark jobs as applied: move the resume folder to customized/submitted/, update seen_jobs, add a sheet row.

    python -m apply_agent.prepare.applied --list                 # tailored jobs not applied yet
    python -m apply_agent.prepare.applied pinterest "clay_swe"   # match by company or folder name
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from apply_agent.core.config import ROOT, load_config, tz
from apply_agent.core.models import Job
from apply_agent.core.resume_text import RESUME
from apply_agent.core.state import SeenJobs

from . import sheets

SUBMITTED = RESUME / "customized" / "submitted"


def active(seen: SeenJobs) -> list[tuple[str, dict]]:
    return sorted(((k, v) for k, v in seen.by_status("customized").items() if not v.get("legacy")),
                  key=lambda kv: kv[1].get("resume_dir", ""))


def find(seen: SeenJobs, query: str) -> list[tuple[str, dict]]:
    q = query.lower().replace(" ", "")
    return [(k, v) for k, v in active(seen)
            if q in (v.get("company") or "").lower().replace(" ", "")
            or q in Path(v.get("resume_dir") or "").name.lower() or q == k.lower()]


def mark(queries: list[str], cfg, dry_run: bool = False) -> list[Job]:
    from apply_agent.core.config import DATA
    if (DATA / "run.lock").exists():
        print("✗ a pipeline run is in progress; mark applied after it finishes (moving folders mid-run loses work)")
        return []
    seen = SeenJobs()
    done: list[Job] = []
    for query in queries:
        hits = find(seen, query)
        if len(hits) != 1:
            names = ", ".join(Path(v.get("resume_dir", "")).name for _, v in hits) or "nothing"
            print(f"✗ '{query}' matched {len(hits)} jobs ({names}); be more specific")
            continue
        jid, e = hits[0]
        src = ROOT / e["resume_dir"]
        jp = src / "job.json"
        job = Job.model_validate_json(jp.read_text()) if jp.exists() else \
            Job(id=jid, source="?", company=e.get("company", ""), title=e.get("title", ""), url=e.get("url", ""))
        dest = SUBMITTED / src.name
        n = 2
        while dest.exists():
            dest = SUBMITTED / f"{src.name}-{n}"
            n += 1
        if dry_run:
            print(f"would mark applied: {job.company} · {job.title} ({src.name} → submitted/{dest.name})")
            continue
        SUBMITTED.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        job.resume_dir = str(dest.relative_to(ROOT))
        seen.set(jid, "applied", resume_dir=job.resume_dir,
                 applied_on=datetime.now(tz(cfg)).strftime("%Y-%m-%d"))
        done.append(job)
        print(f"✓ applied: {job.company} · {job.title} → submitted/{dest.name}")
    seen.save()
    if done and cfg.sheet.enabled:
        try:
            print(f"✓ sheet: {sheets.write(done, cfg)} rows added")
        except Exception as ex:
            print(f"✗ sheet not updated ({type(ex).__name__}: {ex}); applied state is saved")
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.list or not a.queries:
        for k, v in active(SeenJobs()):
            print(f"  {Path(v.get('resume_dir', '')).parent.name}/{Path(v.get('resume_dir', '')).name:<28} "
                  f"{v.get('company')} · {v.get('title')}")
    else:
        mark(a.queries, cfg, a.dry_run)
