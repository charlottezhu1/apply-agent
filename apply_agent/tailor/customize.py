"""Run /customize-batch per job in parallel headless Claude Code sessions, then verify independently."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

from apply_agent.core import llm
from apply_agent.core.config import ROOT, log_error, today_str
from apply_agent.core.models import Job
from apply_agent.core.profile import resume_tag
from apply_agent.core.resume_text import RESUME, confirmed_facts, sync_facts_mirror

from . import factcheck

STAGE = "customize"
ALLOWED_TOOLS = ("Read Edit Write Glob Grep Bash(diff:*) Bash(pdflatex:*) "
                 "Bash(pdftotext:*) Bash(pdfinfo:*) Bash(grep:*) Bash(wc:*) Bash(cat:*) Bash(ls:*)")


def company_slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]", "", name) or "Company"


def folder_for(job: Job, cfg) -> tuple[str, Path]:
    """Returns (company arg, folder). New resumes go to customized/<YYYY-MM-DD>/<Company>_<ROLE>/.
    A job that already has a folder (re-tailor after answers) keeps it."""
    if job.resume_dir and (ROOT / job.resume_dir).exists():
        f = ROOT / job.resume_dir
        return f.name.rsplit("_", 1)[0], f
    company = company_slug(job.company)
    day = RESUME / "customized" / today_str(cfg)
    for cand in (company, f"{company}-{re.sub(r'[^A-Za-z0-9]', '', job.posting_id)[-4:]}"):
        f = day / f"{cand}_{job.role_type}"
        status = f / "status.json"
        owner = json.loads(status.read_text()).get("job_id") if status.exists() else None
        if not f.exists() or owner == job.id or (owner is None and not any(f.glob("*.tex"))):
            return cand, f
    cand = f"{company}-{job.posting_id[-6:]}"
    return cand, day / f"{cand}_{job.role_type}"


def write_jd(job: Job, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    gaps = "\n".join(f"- {g}" for g in job.gaps) or "- (none)"
    jd = (f"Company: {job.company}\nTitle: {job.title}\nURL: {job.url}\nLocations: {'; '.join(job.locations)}\n\n"
          f"<job_posting>\n{job.description or '(no description available)'}\n</job_posting>\n\n"
          f"MATCHMAKER GAPS (hints only, from an automated screen):\n{gaps}\n")
    p = folder / "jd.txt"
    p.write_text(jd, encoding="utf-8")
    # The full record, so step 0 can re-tailor this job after new answers.
    (folder / "job.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
    return p


def base_tex_for(job: Job, cfg) -> Path:
    return ROOT / getattr(cfg.candidate.base_resumes, job.role_type)


def write_changes(base_tex: str, new_tex: str, base_rel: str, out: Path) -> int:
    """changes.txt straight from the diff (no LLM): one entry per changed line, in customization.md's format."""
    import difflib
    a, b = base_tex.splitlines(), new_tex.splitlines()
    lines = [f"{out.parent.name}, customized from {base_rel} (generated from the diff)", ""]
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old, new = a[i1:i2], b[j1:j2]
        for k in range(max(len(old), len(new))):
            o = old[k].strip() if k < len(old) else "(none)"
            w = new[k].strip() if k < len(new) else "(removed)"
            lines += [f"[{i1 + k + 1}] {o}", f"  -> {w}", ""]
            n += 1
    out.write_text("\n".join(lines), encoding="utf-8")
    return n


def verify(job: Job, folder: Path, company: str, cfg) -> Job:
    """Independent checks; sets customize_status / warnings. Never trusts the session's own report."""
    tex = folder / f"{company}_{job.role_type}_{resume_tag()}_Resume.tex"
    pdf = tex.with_suffix(".pdf")
    problems = []
    status = {}
    try:
        status = json.loads((folder / "status.json").read_text())
    except Exception:
        problems.append("status.json missing or invalid")
    if not tex.exists():
        problems.append("tex missing")
    if not pdf.exists():
        problems.append("pdf missing")
    elif factcheck.pdf_pages(pdf) != 1:
        problems.append(f"pdf has {factcheck.pdf_pages(pdf)} pages")
    base = base_tex_for(job, cfg)
    if tex.exists():
        new_t, base_t = tex.read_text(encoding="utf-8"), base.read_text(encoding="utf-8")
        scaf = factcheck.scaffolding_changed(base_t, new_t)
        if scaf:
            problems.append(f"LaTeX scaffolding changed: {scaf[0][:80]}")
        job.warnings = factcheck.warnings(base_t, new_t, confirmed_facts(),
                                          list(getattr(cfg, "factcheck_allowlist", [])))
        if write_changes(base_t, new_t, str(base.relative_to(RESUME)), folder / "changes.txt") == 0:
            job.warnings.append("no edits made (resume identical to base)")
        if pdf.exists():
            base_pdf = base.with_suffix(".pdf")
            if base_pdf.exists() and factcheck.rendered_lines(pdf) > factcheck.rendered_lines(base_pdf):
                job.warnings.append("rendered line count above base")
    if status.get("status") == "failed":
        problems.append(f"session reported failure: {status.get('note', '')}")
    # clean LaTeX leftovers and scratch files
    for pat in ("*.aux", "*.log", "*.out", "*.fls", "*.fdb_latexmk", "base.txt", "new.txt"):
        for f in folder.glob(pat):
            f.unlink(missing_ok=True)
    status["job_id"] = job.id
    (folder / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False))
    job.resume_dir = str(folder.relative_to(ROOT))
    job.resume_pdf = str(pdf.relative_to(ROOT)) if pdf.exists() else None
    if problems:
        job.customize_status = "failed"
        job.warnings = problems + job.warnings
    else:
        job.customize_status = "needs_answers" if status.get("questions") else "ok"
    job.extra["questions_raw"] = status.get("questions") or []
    return job


def session_env(cfg) -> dict:
    """auth: subscription → drop the API key so Claude Code bills the Claude subscription
    (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`, or the normal login). auth: api_key → keep it."""
    env = os.environ.copy()
    if getattr(cfg.customize, "auth", "subscription") == "subscription":
        env.pop("ANTHROPIC_API_KEY", None)
    return env


async def run_session(job: Job, cfg, rd: Path) -> Job:
    llm.USAGE.check()
    company, folder = folder_for(job, cfg)
    job.resume_dir = str(folder.relative_to(ROOT))  # saved in job.json so a re-tailor reuses this folder
    jd = write_jd(job, folder)
    (folder / "status.json").unlink(missing_ok=True)
    # Start from an exact copy of the base (Claude Code refuses `cp` in dontAsk mode, and retyping risks drift).
    shutil.copyfile(base_tex_for(job, cfg), folder / f"{company}_{job.role_type}_{resume_tag()}_Resume.tex")
    cmd = ["claude", "-p", f"/customize-batch {company} {job.role_type.lower()} {jd.relative_to(RESUME)}",
           "--model", cfg.customize.model, "--permission-mode", "dontAsk", "--allowedTools", ALLOWED_TOOLS,
           "--max-budget-usd", str(cfg.customize.max_budget_usd_per_job), "--output-format", "json",
           "--no-session-persistence"]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=RESUME, env=session_env(cfg), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=cfg.customize.timeout_minutes * 60)
    except asyncio.TimeoutError:
        proc.kill()
        log_error(rd, STAGE, job.id, "timeout")
        out, err = b"", b"timeout"
    cost = 0.0
    try:
        res = json.loads(out.decode() or "{}")
        cost = float(res.get("total_cost_usd") or 0)
        if res.get("is_error"):
            log_error(rd, STAGE, job.id, f"session error: {str(res.get('result'))[:500]}")
    except json.JSONDecodeError:
        log_error(rd, STAGE, job.id, f"bad session output: {out[:300]!r} {err[:300]!r}")
    if getattr(cfg.customize, "auth", "subscription") == "api_key":
        llm.USAGE.add_usd(cost)  # subscription sessions don't cost API dollars
    job.extra["customize_cost_usd"] = cost
    return verify(job, folder, company, cfg)


async def run_all(jobs: list[Job], cfg, rd: Path) -> list[Job]:
    if not shutil.which("claude"):
        raise RuntimeError("claude CLI not on PATH")
    sync_facts_mirror()  # sessions read resume/confirmed_facts.md, a copy of applicant/facts.md
    res = await llm.gather_limited([run_session(j, cfg, rd) for j in jobs], cfg.customize.concurrency)
    out = []
    for j, r in zip(jobs, res):
        if isinstance(r, Exception):
            log_error(rd, STAGE, j.id, f"{type(r).__name__}: {r}")
            j.customize_status = "failed"
            j.warnings = [f"{type(r).__name__}: {r}"]
            out.append(j)
        else:
            out.append(r)
    return out
