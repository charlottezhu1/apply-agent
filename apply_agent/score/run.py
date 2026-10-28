"""Stage 2: one Claude call per job, in parallel. Keeps jobs that meet the hard needs AND score ≥ min_score.

    python -m apply_agent.score.run --run-dir runs/2026-10-03 [--limit N] [--dry-run]
    python -m apply_agent.score.run --eval tests/labeled.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import BaseModel

from apply_agent.core import llm
from apply_agent.core.config import load_config, log_error, run_dir as default_run_dir
from apply_agent.core.models import Job, read_jobs, write_jobs
from apply_agent.core.profile import load_profile
from apply_agent.core.resume_text import base_resume_text, confirmed_facts, preferences
from apply_agent.core.state import SeenJobs

STAGE = "matchmaker"
PROMPT = Path(__file__).parent / "prompt.md"
ROLE_LABEL = {"SWE": "Software Engineer (new grad / entry level)", "PM": "Product Manager / Associate PM (new grad)"}
LOCATION_RULE = {
    "bay_area_only": "San Francisco Bay Area only. Other locations fail.",
    "bay_area_or_remote": "San Francisco Bay Area, or remote within the US. A role that offers a Bay Area office "
                          "among several locations passes. Roles only outside the Bay Area and not US-remote fail.",
    "us": "Anywhere in the US (Bay Area preferred). Non-US-only roles fail.",
}
EMPTY_JD_CAP = 60


class Verdict(BaseModel):
    role_ok: bool
    level_ok: bool
    location_ok: bool
    sponsorship_ok: bool
    preferences_ok: bool
    needs_reasons: str
    match_score: int
    alignment: list[str]
    gaps: list[str]


def build_prompts(job: Job, cfg) -> tuple[str, str]:
    sys_t, user_t = PROMPT.read_text(encoding="utf-8").split("<!-- USER -->")
    sys_t = sys_t.replace("<!-- SYSTEM -->", "").strip()
    role = job.role_type or "SWE"
    system = sys_t.format(
        role_label=ROLE_LABEL[role],
        resume_text=base_resume_text(getattr(cfg.candidate.base_resumes, role)),
        facts=confirmed_facts(),
        preferences=preferences(),
        location_rule=LOCATION_RULE[cfg.search.location_mode],
        **load_profile().prompt_vars(),
    )
    user = user_t.strip().format(
        company=job.company, title=job.title, locations="; ".join(job.locations) or "not stated",
        posted_at=job.posted_at.date().isoformat() if job.posted_at else "unknown",
        description=job.description or "(No job description available. Judge from the title only.)",
    )
    return system, user


def apply_verdict(job: Job, v: Verdict) -> Job:
    job.match_score = max(0, min(100, v.match_score))
    if not job.description:
        job.match_score = min(job.match_score, EMPTY_JD_CAP)
        v.gaps = (v.gaps + ["JD unavailable — verify manually"])[:5]
    job.alignment, job.gaps = v.alignment[:5], v.gaps[:5]
    job.meets_needs = v.role_ok and v.level_ok and v.location_ok and v.sponsorship_ok and v.preferences_ok
    job.needs_reasons = v.needs_reasons
    return job


async def score(job: Job, cfg) -> Job:
    system, user = build_prompts(job, cfg)
    v = await llm.structured(cfg.matchmaker.model, system, user, Verdict,
                             effort=cfg.matchmaker.effort, max_tokens=8000)
    return apply_verdict(job, v)


async def score_all(jobs: list[Job], cfg) -> list:
    """Score one job per role first so its system prompt lands in the provider's prompt cache,
    then the rest in parallel (otherwise the first parallel wave all misses the cache)."""
    firsts: dict[str, int] = {}
    for i, j in enumerate(jobs):
        firsts.setdefault(j.role_type or "SWE", i)
    results: list = [None] * len(jobs)
    for i in firsts.values():
        try:
            results[i] = await score(jobs[i], cfg)
        except Exception as e:
            results[i] = e
    rest = [i for i in range(len(jobs)) if i not in firsts.values()]
    for i, r in zip(rest, await llm.gather_limited([score(jobs[i], cfg) for i in rest], cfg.matchmaker.concurrency)):
        results[i] = r
    return results


def main(rd: Path, cfg=None, limit: int | None = None, dry_run: bool = False) -> Path:
    cfg = cfg or load_config()
    jobs = read_jobs(rd / "01_listed.json")
    seen = SeenJobs()
    jobs = [j for j in jobs if not (seen.get(j.id) or {}).get("status") in ("matched", "rejected")]
    cap = limit or cfg.matchmaker.max_jobs_per_run
    if len(jobs) > cap:
        print(f"[matchmaker] capping {len(jobs)} jobs to {cap} (newest first); the rest wait for the next run")
        jobs = jobs[:cap]
    if dry_run:
        if jobs:
            s, u = build_prompts(jobs[0], cfg)
            print(f"--- SYSTEM ({len(s)} chars) ---\n{s}\n--- USER ---\n{u[:3000]}")
        return rd / "02_matched.json"

    results = asyncio.run(score_all(jobs, cfg))
    kept, rejected = [], []
    for job, res in zip(jobs, results):
        if isinstance(res, Exception):
            log_error(rd, STAGE, job.id, f"{type(res).__name__}: {res}")
            continue  # not marked seen → retried next run
        ok = res.meets_needs and res.match_score >= cfg.matchmaker.min_score
        (kept if ok else rejected).append(res)
        seen.set(res.id, "matched" if ok else "rejected", fingerprint=res.fingerprint,
                 match_score=res.match_score, company=res.company, title=res.title, url=res.url)
    seen.save()

    # Merge with jobs kept earlier in the same run (re-runs are idempotent).
    prev = {j.id: j for j in read_jobs(rd / "02_matched.json")}
    prev.update({j.id: j for j in kept})
    kept_all = sorted(prev.values(), key=lambda j: -(j.match_score or 0))
    write_jobs(rd / "02_matched.json", kept_all)
    write_jobs(rd / "02_rejected.json", read_jobs(rd / "02_rejected.json") + rejected)
    usage = llm.USAGE.as_dict()
    (rd / "matchmaker_usage.json").write_text(json.dumps(usage, indent=2))
    errs = len(jobs) - len(kept) - len(rejected)
    hit = 100 * usage["cache_read_tokens"] / max(usage["cache_read_tokens"] + usage["input_tokens"], 1)
    print(f"[matchmaker] scored {len(kept) + len(rejected)} · kept {len(kept)} · rejected {len(rejected)} · "
          f"errors {errs} · ${usage['usd']:.2f} · cache hits {hit:.0f}% of input tokens")
    return rd / "02_matched.json"


def run_eval(path: Path, cfg) -> None:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    jobs = [Job(id=r.get("id") or f"eval:{i}", source="eval", company=r["company"], title=r["title"],
                url=r.get("url", ""), description=r.get("description", ""), role_type=r.get("role_type", "SWE"),
                locations=r.get("locations", [])) for i, r in enumerate(rows)]
    res = asyncio.run(llm.gather_limited([score(j, cfg) for j in jobs], cfg.matchmaker.concurrency))
    tp = tn = fp = fn = 0
    for r, j in zip(rows, res):
        if isinstance(j, Exception):
            print(f"ERROR {r['title']}: {j}")
            continue
        keep = j.meets_needs and j.match_score >= cfg.matchmaker.min_score
        exp = r["expected_keep"]
        tp += keep and exp
        tn += (not keep) and (not exp)
        fp += keep and not exp
        fn += (not keep) and exp
        if keep != exp:
            print(f"✗ {r['company']} · {r['title']}: expected {'keep' if exp else 'drop'}, got score "
                  f"{j.match_score}, needs={j.meets_needs} ({j.needs_reasons})")
    print(f"keep✓ {tp}  drop✓ {tn}  false-keep {fp}  false-drop {fn}  · ${llm.USAGE.usd:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--eval", type=Path)
    a = ap.parse_args()
    cfg = load_config()
    if a.eval:
        run_eval(a.eval, cfg)
    else:
        main(a.run_dir or default_run_dir(cfg), cfg, a.limit, a.dry_run)
