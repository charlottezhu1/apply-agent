"""Stage 1: fetch postings from every board + Simplify, filter, write 01_listed.json.

    python -m apply_agent.discover.run --run-dir runs/2026-10-03 [--limit-companies N] [--only stripe,notion]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from apply_agent.core.config import load_config, log_error, run_dir as default_run_dir
from apply_agent.core.models import Job, write_jobs
from apply_agent.core.state import SeenJobs

from . import filters as F
from .discover import load_companies
from .sources.ats import SOURCES
from .sources.base import Http
from .sources.simplify import Simplify

STAGE = "listener"


def apply(fs, job: Job, cfg) -> str | None:
    for f in fs:
        reason = f(job, cfg)
        if reason:
            return reason
    return None


async def fetch_all(cfg, rd: Path, companies: list[dict]) -> list[Job]:
    http = Http()

    async def one(c):
        try:
            return await SOURCES[c["ats"]].list_jobs(http, c)
        except Exception as e:
            log_error(rd, STAGE, f"{c['ats']}:{c.get('token') or c.get('tenant')}", f"{type(e).__name__}: {e}")
            return []

    results = await asyncio.gather(*(one(c) for c in companies))
    jobs = [j for r in results for j in r]
    if getattr(cfg.search, "use_simplify", True):
        try:
            jobs += await Simplify().list_jobs(http)
        except Exception as e:
            log_error(rd, STAGE, "simplify", str(e))
    await http.aclose()
    return jobs


async def fetch_details(jobs: list[Job], rd: Path) -> None:
    http = Http()
    sources = {**SOURCES, "simplify": Simplify()}

    async def one(j: Job):
        if j.description:
            return
        try:
            await sources[j.source].fetch_detail(http, j)
        except Exception as e:
            log_error(rd, STAGE, j.id, f"detail: {type(e).__name__}: {e}")

    await asyncio.gather(*(one(j) for j in jobs))
    await http.aclose()


def main(rd: Path, cfg=None, limit_companies: int | None = None, only: list[str] | None = None) -> Path:
    cfg = cfg or load_config()
    rd.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    seen = SeenJobs()
    companies = [c for c in load_companies() if c.get("enabled", True)]
    if only:
        companies = [c for c in companies
                     if (c.get("token") or c.get("tenant") or "").lower() in only or c["name"].lower() in only]
    if limit_companies:
        companies = companies[:limit_companies]

    raw = asyncio.run(fetch_all(cfg, rd, companies))
    counts = {"fetched": len(raw)}
    drops = []

    def drop(j: Job, reason: str):
        counts[reason] = counts.get(reason, 0) + 1
        drops.append({"id": j.id, "company": j.company, "title": j.title, "reason": reason})

    # 1. dedupe: exact ids, Simplify items that point at a posting we fetched directly, then seen jobs
    by_id: dict[str, Job] = {}
    for j in raw:
        by_id.setdefault(j.id, j)
    ats_ids = set(by_id)
    stage1: list[Job] = []
    for j in by_id.values():
        if j.source == "simplify" and (j.extra.get("ats_id") in ats_ids):
            continue
        if seen.is_seen(j.id, j.fingerprint):
            drop(j, "seen")
            continue
        stage1.append(j)

    # 2. cheap filters (title / location / date)
    stage2 = []
    for j in stage1:
        r = apply(F.PRE_FETCH, j, cfg)
        if r:
            drop(j, r)
        else:
            stage2.append(j)

    # 3. same role in several cities (or on two sources): keep one, preferring Bay Area + direct ATS
    best: dict[str, Job] = {}
    for j in stage2:
        cur = best.get(j.fingerprint)
        rank = (j.bay_area, j.source != "simplify", bool(j.description))
        if cur is None or rank > (cur.bay_area, cur.source != "simplify", bool(cur.description)):
            if cur is not None:
                drop(cur, "duplicate")
            best[j.fingerprint] = j
        else:
            drop(j, "duplicate")
    stage3 = list(best.values())

    # 4. fetch JDs only for survivors, then JD-based filters
    asyncio.run(fetch_details(stage3, rd))
    kept = []
    for j in stage3:
        r = apply(F.POST_FETCH, j, cfg)
        if r:
            drop(j, r)
        else:
            kept.append(j)

    kept.sort(key=lambda j: (j.posted_at is None, -(j.posted_at.timestamp() if j.posted_at else 0)))
    out = write_jobs(rd / "01_listed.json", kept)
    with open(rd / "listener_drops.jsonl", "w", encoding="utf-8") as f:
        for d in drops:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    order = ["fetched", "seen", "role", "seniority", "internship", "location", "age", "duplicate", "years",
             "sponsorship"]
    summary = " · ".join(f"{k} {counts[k]:,}" for k in order if k in counts)
    print(f"[listener] {len(companies)} boards + simplify · {summary} → {len(kept)} jobs "
          f"({time.time() - t0:.0f}s)")
    (rd / "listener_summary.json").write_text(json.dumps({**counts, "kept": len(kept)}, indent=2))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--limit-companies", type=int)
    ap.add_argument("--only", help="comma-separated tokens or names")
    a = ap.parse_args()
    cfg = load_config()
    main(a.run_dir or default_run_dir(cfg), cfg, a.limit_companies,
         [s.strip().lower() for s in a.only.split(",")] if a.only else None)
