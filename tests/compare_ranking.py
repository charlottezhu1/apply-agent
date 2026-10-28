"""Write rank_compare.md: the same jobs ranked under 5 weight settings (see TEST.md §1).

    python -m tests.compare_ranking --run-dir runs/2026-10-03 [--now 2026-10-03] [--n 20]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from apply_agent.core.config import load_config
from apply_agent.core.models import read_jobs

from apply_agent.tailor.rank import rank

SETTINGS = [("A", 1.0, 14), ("B", 0.8, 14), ("C", 0.7, 14), ("D", 0.6, 7), ("E", 0.5, 3)]


def main(rd: Path, now: datetime, n: int) -> Path:
    cfg = load_config()
    jobs = read_jobs(rd / "02_matched.json")
    results = {name: rank(jobs, None, cfg, now, n=n, match_weight=w, half_life_days=h) for name, w, h in SETTINGS}
    base = {j.id for j in results["C"]}
    lines = [f"# Rank comparison · {len(jobs)} matched jobs · top {n} · now={now.date()}\n"]
    for name, w, h in SETTINGS:
        ids = {j.id for j in results[name]}
        lines.append(f"## {name}: match_weight={w}, half_life={h}d"
                     + ("  (default)" if name == "C" else f"  · vs C: +{len(ids - base)} / −{len(base - ids)}"))
        lines.append("| # | company | title | match | age (d) | score |\n|---|---|---|---|---|---|")
        for j in results[name]:
            age = f"{(now - j.posted_at).days}" if j.posted_at else "?"
            mark = "" if j.id in base else " ➕"
            lines.append(f"| {j.rank} | {j.company} | {j.title[:60]}{mark} | {j.match_score} | {age} | {j.rank_score:.3f} |")
        lines.append("")
    out = rd / "rank_compare.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--now")
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now).replace(tzinfo=timezone.utc) if a.now else datetime.now(timezone.utc)
    main(a.run_dir, now, a.n)
