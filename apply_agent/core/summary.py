"""runs/<date>/SUMMARY.md: the one page the pop-up opens."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from .config import ROOT, RUNS, load_config
from .models import Job, read_jobs
from .state import SeenJobs


TODAY_MIN_SCORE = 65  # a job posted today goes to the top of the summary only above this match score


def _question_trend() -> str:
    today = date.today()
    weeks = {0: [], 1: []}
    for f in RUNS.glob("*/customize_usage.json"):
        for row in json.loads(f.read_text()):
            age = (today - date.fromisoformat(row["date"])).days
            if age < 14:
                weeks[age // 7].append(row["questions"])
    avg = lambda xs: f"{sum(xs) / len(xs):.1f}" if xs else "–"
    return f"Questions per resume: {avg(weeks[0])} this week ({avg(weeks[1])} last week)"


def _ranked(jobs: list[Job]) -> list[Job]:
    """Best first by match + recency (same formula and weights as the tailoring rank)."""
    from apply_agent.tailor.rank import rank_score
    cfg = load_config()
    now = datetime.now(timezone.utc)
    for j in jobs:
        j.rank_score = round(rank_score(j, now, cfg.rank.match_weight, cfg.rank.half_life_days), 4)
    return sorted(jobs, key=lambda j: (-j.rank_score, -(j.match_score or 0),
                                       -(j.posted_at.timestamp() if j.posted_at else 0), j.id))


def _posted_today(j: Job, today: date) -> bool:
    tz = ZoneInfo(load_config().timezone)
    return bool(j.posted_at and j.posted_at.astimezone(tz).date() == today
                and (j.match_score or 0) > TODAY_MIN_SCORE)


def _job_block(j: Job, n: int | str) -> str:
    posted = j.posted_at.date().isoformat() if j.posted_at else "date unknown"
    lines = [f"### {n}. {j.company} · {j.title} · match {j.match_score} · posted {posted}",
             f"- Apply: {j.url}",
             f"- Resume: `{j.resume_pdf}`  (status: {j.customize_status})"]
    if j.warnings:
        lines.append("- ⚠️ " + "; ".join(j.warnings))
    if j.open_questions:
        lines.append(f"- Open resume questions: {', '.join(j.open_questions)}")
    if j.fill_by_hand:
        lines.append("- Fill by hand: " + "; ".join(q[:80] for q in j.fill_by_hand))
    if j.answers:
        lines.append("- Drafted answers (review before pasting; the extension fills them):")
        for q, a in j.answers.items():
            lines.append(f"  - **{q}**\n\n    {a.replace(chr(10), chr(10) + '    ')}\n")
    if j.sheet_row:
        lines.append(f"- Sheet row: {j.sheet_row}")
    lines.append(f"- Applied? Move `{Path(j.resume_dir or '').name}` into `resume/customized/submitted/`")
    return "\n".join(lines)


def write(rd: Path, *, spent: float, n_open_questions: int, closed: list[str], failed_step: str | None) -> Path:
    jobs = read_jobs(rd / "04_packets.json") or read_jobs(rd / "03_top.json")
    new = _ranked([j for j in jobs if j.rank])
    fresh = [j for j in new if _posted_today(j, date.today())]
    new = [j for j in new if j not in fresh]
    retailored = [j for j in jobs if not j.rank]
    failures = [json.loads(l) for l in (rd / "errors.jsonl").read_text().splitlines()] \
        if (rd / "errors.jsonl").exists() else []
    seen = SeenJobs()
    today_ids = {j.id for j in jobs}
    backlog = {k: v for k, v in seen.by_status("customized").items()
               if not v.get("legacy") and k not in today_ids}
    out = [f"# apply_agent · {rd.name}",
           f"**{len(new) + len(fresh)} new · {n_open_questions} open questions · {len(failures)} errors · ${spent:.2f} spent**  ",
           _question_trend(), ""]
    if failed_step:
        out += [f"> ❌ Run stopped at **{failed_step}**. See `{rd / 'run.log'}`.", ""]
    out += ["## Questions for you",
            f"{n_open_questions} open in `resume/questions.md`. Answer after `- Answer:`; the next run absorbs them.", ""]
    n = 0
    if fresh:
        out += [f"## 🔥 Posted today (match > {TODAY_MIN_SCORE}): apply to these first"]
        for j in fresh:
            n += 1
            out.append(_job_block(j, n) + "\n")
    if new:
        out += ["## New (ranked by match score + recency)"]
        for j in new:
            n += 1
            out.append(_job_block(j, n) + "\n")
    elif not fresh:
        out += ["## New today", "_None._", ""]
    if retailored:
        out += ["## Re-tailored after your answers", *(_job_block(j, "↻") + "\n" for j in retailored)]
    out += ["## Backlog (tailored earlier, not applied yet)"]
    out += [f"- {v.get('company')} · {v.get('title')} · {v.get('url')} · `{v.get('resume_dir')}` · since {v.get('first_seen')}"
            for v in backlog.values()] or ["_Empty._"]
    out += ["", "## Closed since last run (don't apply)"]
    out += [f"- {seen.jobs[k].get('company')} · {seen.jobs[k].get('title')}" for k in closed] or ["_None._"]
    out += ["", "## Errors"]
    out += [f"- {f['stage']} · {f.get('job_id') or ''} · {f['error'][:160]}" for f in failures[:30]] or ["_None._"]
    p = rd / "SUMMARY.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p
