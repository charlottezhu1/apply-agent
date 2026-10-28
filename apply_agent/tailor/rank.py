"""Pure ranking: match score + recency, per-company cap, top jobs_per_day."""
from __future__ import annotations

from datetime import datetime, timezone

from apply_agent.core.models import Job
from apply_agent.core.state import SeenJobs


def recency(job: Job, now: datetime, half_life_days: float) -> float:
    if job.posted_at is None:
        return 0.5
    age_days = max(0.0, (now - job.posted_at).total_seconds() / 86400)
    return 0.5 ** (age_days / half_life_days)


def rank_score(job: Job, now: datetime, match_weight: float, half_life_days: float) -> float:
    return match_weight * (job.match_score or 0) / 100 + (1 - match_weight) * recency(job, now, half_life_days)


def rank(jobs: list[Job], seen: SeenJobs | None, cfg, now: datetime | None = None, *,
         n: int | None = None, match_weight: float | None = None, half_life_days: float | None = None) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    w = cfg.rank.match_weight if match_weight is None else match_weight
    h = cfg.rank.half_life_days if half_life_days is None else half_life_days
    n = cfg.jobs_per_day if n is None else n
    pool = []
    for j in jobs:
        if seen and (seen.is_done(j.id, j.fingerprint) or seen.legacy_block(j.company, j.role_type)):
            continue
        j = j.model_copy()
        j.rank_score = round(rank_score(j, now, w, h), 4)
        pool.append(j)
    pool.sort(key=lambda j: (-j.rank_score, -(j.match_score or 0),
                             -(j.posted_at.timestamp() if j.posted_at else 0), j.id))
    out, per_company = [], {}
    for j in pool:
        c = j.company.lower()
        if per_company.get(c, 0) >= cfg.rank.max_per_company:
            continue
        per_company[c] = per_company.get(c, 0) + 1
        j.rank = len(out) + 1
        out.append(j)
        if len(out) >= n:
            break
    return out
