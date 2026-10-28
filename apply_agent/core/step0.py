"""Step 0 of each run: mark applied / closed / expired jobs, and seed state from pre-pipeline folders."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta

from .config import ROOT
from .resume_text import RESUME
from .state import SeenJobs, company_role_key

CUSTOMIZED = RESUME / "customized"
SUBMITTED = CUSTOMIZED / "submitted"
EXISTED = CUSTOMIZED / "existed"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def backfill() -> int:
    """Idempotent: record folders that existed before the pipeline (customized/existed/ and submitted/ folders
    without a job.json) so the same company + role isn't re-tailored for 60 days."""
    seen = SeenJobs()
    n = 0
    for base, status in ((EXISTED, "customized"), (SUBMITTED, "applied")):
        if not base.exists():
            continue
        for f in base.iterdir():
            if not f.is_dir() or (f / "job.json").exists():
                continue
            if status == "customized" and (SUBMITTED / f.name).exists():
                continue  # same name in submitted/ → applied wins
            m = re.match(r"(.+?)_(SWE|PM|MLE|FDE|AI_ENGINEER|PM\w*)$", f.name)
            company, role = (m.group(1), m.group(2)) if m else (f.name, "")
            role = "SWE" if role in ("MLE", "FDE", "AI_ENGINEER") else role
            jid = f"legacy:{f.name}"
            rel = str(f.relative_to(ROOT))
            e = seen.jobs.get(jid)
            if e and e["status"] == status and e.get("resume_dir") == rel:
                continue
            seen.set(jid, status, legacy=True, legacy_key=company_role_key(company, role), company=company,
                     resume_dir=rel)
            n += 1
    seen.save()
    return n


def mark_applied(seen: SeenJobs) -> list[str]:
    """A folder moved into customized/submitted/ means the applicant applied. Matched by the job.json inside it
    (folder names can repeat across days), falling back to the folder name."""
    by_id, by_name = {}, {}
    if SUBMITTED.exists():
        for f in SUBMITTED.iterdir():
            jp = f / "job.json"
            if jp.exists():
                try:
                    by_id[json.loads(jp.read_text())["id"]] = f
                except Exception:
                    pass
            by_name[f.name] = f
    done = []
    for jid, e in seen.by_status("customized").items():
        if e.get("legacy"):
            continue
        name = (e.get("resume_dir") or "").rsplit("/", 1)[-1]
        f = by_id.get(jid) or (by_name.get(name) if not (ROOT / (e.get("resume_dir") or "x")).exists() else None)
        if f:
            seen.set(jid, "applied", resume_dir=str(f.relative_to(ROOT)))
            done.append(jid)
    return done


def expire(seen: SeenJobs, backlog_days: int) -> list[str]:
    cutoff = (date.today() - timedelta(days=backlog_days)).isoformat()
    out = []
    for jid, e in seen.by_status("customized").items():
        if e.get("updated", "") < cutoff:
            seen.set(jid, "expired")
            out.append(jid)
    return out


async def _is_closed(http, jid: str, e: dict) -> bool:
    src, _, rest = jid.partition(":")
    token, _, pid = rest.rpartition(":")
    if src == "greenhouse":
        r = await http.request("GET", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{pid}")
        return r.status_code == 404
    if src == "lever":
        r = await http.request("GET", f"https://api.lever.co/v0/postings/{token}/{pid}")
        return r.status_code == 404
    if src == "ashby":
        r = await http.request("GET", f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        return r.status_code == 200 and pid not in r.text
    url = e.get("url")
    if not url:
        return False
    r = await http.request("GET", url)
    return r.status_code in (404, 410)


def mark_closed(seen: SeenJobs) -> list[str]:
    from apply_agent.discover.sources.base import Http

    todo = {k: v for k, v in seen.by_status("customized").items() if not v.get("legacy")}

    async def run():
        http = Http()
        res = await asyncio.gather(*(_is_closed(http, k, v) for k, v in todo.items()), return_exceptions=True)
        await http.aclose()
        return res

    closed = [k for k, r in zip(todo, asyncio.run(run()) if todo else []) if r is True]
    for k in closed:
        seen.set(k, "closed")
    return closed
