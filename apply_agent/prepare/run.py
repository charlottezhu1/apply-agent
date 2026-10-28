"""Stage 4: draft essay answers, write tracker rows, build autofill packets for the extension.

    python -m apply_agent.prepare.run --run-dir runs/2026-10-03 [--dry-run] [--no-sheet]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from apply_agent.core import llm
from apply_agent.core.config import EXT_GENERATED, ROOT, load_config, log_error, run_dir as default_run_dir
from apply_agent.core.models import Job, read_jobs, write_jobs
from apply_agent.core.profile import resume_tag, write_extension_profile
from apply_agent.core.state import SeenJobs
from apply_agent.discover.sources.base import Http

from . import sheets
from .answers import draft
from .questions import essay_questions

STAGE = "preparer"
PACKETS = EXT_GENERATED / "packets.json"


async def prepare(jobs: list[Job], cfg, rd: Path) -> None:
    http = Http()
    qs = await asyncio.gather(*(essay_questions(http, j, cfg.submission.cover_letter) for j in jobs))
    await http.aclose()
    tasks, keys = [], []
    for j, (essays, by_hand) in zip(jobs, qs):
        j.fill_by_hand = by_hand
        for q in essays:
            tasks.append(draft(j, q, cfg))
            keys.append((j, q))
    res = await llm.gather_limited(tasks, cfg.matchmaker.concurrency)
    for (j, q), r in zip(keys, res):
        if isinstance(r, Exception):
            log_error(rd, STAGE, j.id, f"answer '{q[:40]}': {r}")
        else:
            j.answers[q] = r


def match_keys(j: Job) -> list[str]:
    keys = {j.posting_id, j.url}
    tail = (j.url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]  # embedded forms (Greenhouse iframes) carry only the numeric id
    if tail.isdigit():
        keys.add(tail)
    for k in ("gh_id", "lever_id"):
        if j.extra.get(k):
            keys.add(str(j.extra[k]))
    ats = j.extra.get("ats") or {}
    if ats.get("posting_id"):
        keys.add(str(ats["posting_id"]))
    return sorted(k for k in keys if k and len(k) >= 5)


def build_packets() -> int:
    """Packets for every tailored job that isn't applied / closed / expired (today's and the backlog)."""
    seen = SeenJobs()
    packets = []
    for job_id, e in seen.by_status("customized").items():
        folder = ROOT / (e.get("resume_dir") or "")
        jp = folder / "job.json"
        if not e.get("resume_dir") or not jp.exists():
            continue
        j = Job.model_validate_json(jp.read_text())
        answers = json.loads((folder / "answers.json").read_text()) if (folder / "answers.json").exists() else {}
        pdf = next(iter(sorted(folder.glob(f"*_{resume_tag()}_Resume.pdf"), key=lambda p: not p.name.startswith(folder.name))), None)  # tailored copy, not the base
        packets.append({"match": match_keys(j), "company": j.company, "title": j.title, "folder": folder.name,
                        "resume_pdf": str(pdf) if pdf else None, "answers": answers})
    PACKETS.parent.mkdir(parents=True, exist_ok=True)
    PACKETS.write_text(json.dumps(packets, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(packets)


def main(rd: Path, cfg=None, dry_run: bool = False, no_sheet: bool = False) -> Path:
    cfg = cfg or load_config()
    jobs = [j for j in read_jobs(rd / "03_top.json") + read_jobs(rd / "03_retailored.json")
            if j.customize_status in ("ok", "needs_answers")
            and j.resume_dir and (ROOT / j.resume_dir).exists()]  # skip jobs already moved to submitted/
    todo = [j for j in jobs if not (ROOT / j.resume_dir / "answers.json").exists()] if not dry_run else jobs
    if todo and not dry_run:
        asyncio.run(prepare(todo, cfg, rd))
        for j in todo:
            (ROOT / j.resume_dir / "answers.json").write_text(
                json.dumps(j.answers, indent=2, ensure_ascii=False), encoding="utf-8")
            (ROOT / j.resume_dir / "fill_by_hand.json").write_text(json.dumps(j.fill_by_hand, indent=2))
    for j in jobs:  # reload answers for jobs prepared on an earlier pass
        a = ROOT / j.resume_dir / "answers.json"
        if a.exists():
            j.answers = json.loads(a.read_text())
        h = ROOT / j.resume_dir / "fill_by_hand.json"
        if h.exists():
            j.fill_by_hand = json.loads(h.read_text())
    added = 0
    if cfg.sheet.enabled and getattr(cfg.sheet, "write_on", "applied") == "tailored" and not no_sheet:
        try:
            added = sheets.write([j for j in jobs if j.rank], cfg, dry_run=dry_run)
        except Exception as e:
            log_error(rd, STAGE, None, f"sheet: {type(e).__name__}: {e}")
    n_packets = build_packets()
    write_extension_profile()  # the extension's autofill values (applicant/profile.yaml)
    out = write_jobs(rd / "04_packets.json", jobs)
    print(f"[preparer] answers for {len(todo)} jobs · {added} sheet rows · {n_packets} extension packets · "
          f"total spend ${llm.USAGE.usd:.2f}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sheet", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    main(a.run_dir or default_run_dir(cfg), cfg, a.dry_run, a.no_sheet)
