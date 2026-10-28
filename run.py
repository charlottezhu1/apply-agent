"""Daily run: step 0 (state sync) → listener → matchmaker → optimizer → preparer → SUMMARY.md → pop-up.

    python run.py                 # normal daily run (skips if today's run already finished)
    python run.py --force         # run again today
    python run.py --no-popup      # for testing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

from apply_agent import sync
from apply_agent.core import llm, notify, step0, summary
from apply_agent.core.config import APPLICANT, DATA, ROOT, load_config, run_dir
from apply_agent.core.models import read_jobs
from apply_agent.core.profile import ProfileError
from apply_agent.core.state import SeenJobs

LOCK = DATA / "run.lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> bool:
    DATA.mkdir(exist_ok=True)
    if LOCK.exists():
        try:
            if _alive(int(LOCK.read_text().strip())):
                return False
        except ValueError:
            pass
    LOCK.write_text(str(os.getpid()))
    return True


def commit_private_repos(date: str) -> None:
    """resume/ and applicant/ are each their own private git repo (if you made them one): snapshot after every run."""
    for repo in (ROOT / "resume", APPLICANT):
        if not (repo / ".git").exists():
            continue
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", f"apply_agent run {date}"], cwd=repo, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-popup", action="store_true")
    a = ap.parse_args()
    try:
        cfg = load_config()
    except ProfileError as e:
        print(f"✗ {e}")
        return 2
    rd = run_dir(cfg)
    if (rd / "DONE").exists() and not a.force:
        print(f"today's run already finished ({rd}); use --force to run again")
        return 0
    if not acquire_lock():
        print("another run is in progress")
        return 0

    if (rd / "errors.jsonl").exists():  # a --force re-run starts with a clean error list
        (rd / "errors.jsonl").rename(rd / f"errors.before-{os.getpid()}.jsonl")
    logging.basicConfig(filename=rd / "run.log", level=logging.INFO, format="%(asctime)s %(message)s")
    llm.USAGE.budget_usd = cfg.daily_budget_usd
    failed_step, closed = None, []
    try:
        # ---- step 0 ----
        step = "step 0"
        sync.sync()  # applicant/ → extension profile + the facts copy the tailoring sessions read
        step0.backfill()
        seen = SeenJobs()
        applied = step0.mark_applied(seen)
        closed = step0.mark_closed(seen)
        expired = step0.expire(seen, cfg.backlog_days)
        seen.save()
        print(f"[step 0] applied {len(applied)} · closed {len(closed)} · expired {len(expired)}")
        from apply_agent.tailor import absorb, run as optimizer
        folders = absorb.absorb(cfg.submission.model)
        if folders:
            optimizer.retailor(folders, rd, cfg)

        # ---- stages ----
        from apply_agent.discover import run as listener
        from apply_agent.score import run as matchmaker
        from apply_agent.prepare import run as preparer
        for step, fn in (("listener", listener.main), ("matchmaker", matchmaker.main),
                         ("optimizer", optimizer.main), ("preparer", preparer.main)):
            try:
                fn(rd, cfg)
            except llm.BudgetExceeded as e:  # keep going: later stages still handle what's already done
                print(f"[{step}] {e}")
        (rd / "DONE").write_text("ok")
    except Exception:
        failed_step = step
        tb = traceback.format_exc()
        logging.error(tb)
        print(tb, file=sys.stderr)
    finally:
        LOCK.unlink(missing_ok=True)

    from apply_agent.tailor.inbox import Inbox
    n_q = len(Inbox().open)
    s = summary.write(rd, spent=llm.USAGE.usd, n_open_questions=n_q, closed=closed, failed_step=failed_step)
    (rd / "usage.json").write_text(json.dumps(llm.USAGE.as_dict(), indent=2))
    commit_private_repos(rd.name)
    new = sum(j.customize_status in ("ok", "needs_answers") for j in read_jobs(rd / "03_top.json"))
    msg = (f"apply_agent FAILED at {failed_step}" if failed_step else
           f"{new} new jobs ready · {n_q} questions for you · ${llm.USAGE.usd:.2f} spent")
    print(f"[done] {msg} → {s}")
    if not a.no_popup:
        notify.popup(msg, s)
    return 1 if failed_step else 0


if __name__ == "__main__":
    sys.exit(main())
