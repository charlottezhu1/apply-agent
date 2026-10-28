"""Score jobs Claude already judged with another model, and compare. Doesn't touch seen_jobs.

    python -m tests.compare_models --run-dir runs/2026-10-03 --model deepseek-chat --n 40
"""
from __future__ import annotations

import argparse
import asyncio
import random
from pathlib import Path

from apply_agent.core import llm
from apply_agent.core.config import load_config
from apply_agent.core.models import read_jobs

from apply_agent.score.run import score, score_all


def main(rd: Path, model: str, n: int) -> None:
    cfg = load_config()
    kept, rejected = read_jobs(rd / "02_matched.json"), read_jobs(rd / "02_rejected.json")
    random.seed(0)
    sample = random.sample(kept, min(n // 2, len(kept))) + random.sample(rejected, min(n - n // 2, len(rejected)))
    ref = {j.id: (j.meets_needs and (j.match_score or 0) >= cfg.matchmaker.min_score, j.match_score) for j in sample}
    cfg.matchmaker.model = model
    jobs = [j.model_copy(deep=True) for j in sample]
    res = asyncio.run(score_all(jobs, cfg))
    agree = errors = 0
    diffs, rows = [], []
    for j, r in zip(sample, res):
        if isinstance(r, Exception):
            errors += 1
            print(f"ERROR {j.company} · {j.title}: {r}")
            continue
        keep = r.meets_needs and r.match_score >= cfg.matchmaker.min_score
        ck, cs = ref[j.id]
        agree += keep == ck
        diffs.append(abs(r.match_score - (cs or 0)))
        if keep != ck:
            rows.append(f"  ✗ {j.company[:22]:<22} {j.title[:45]:<45} claude {'KEEP' if ck else 'drop'} {cs:>3} · "
                        f"{model} {'KEEP' if keep else 'drop'} {r.match_score:>3}  {r.needs_reasons[:70]}")
    done = len(sample) - errors
    print("\n".join(rows))
    print(f"\n{model} vs Claude on {done} jobs: keep/drop agreement {agree}/{done} "
          f"({100 * agree / max(done, 1):.0f}%) · mean score gap {sum(diffs) / max(len(diffs), 1):.1f} · "
          f"errors {errors} · cost ${llm.USAGE.usd:.3f} (≈ ${llm.USAGE.usd / max(done, 1) * 150:.2f} per 150 jobs)")
    tot = llm.USAGE.input + llm.USAGE.cache_read
    print(f"prompt cache: {llm.USAGE.cache_read:,} of {tot:,} input tokens were cache hits "
          f"({100 * llm.USAGE.cache_read / max(tot, 1):.0f}%)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    main(a.run_dir, a.model, a.n)
