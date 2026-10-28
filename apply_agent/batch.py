"""Triage a long list of job links you collected yourself: drop what's applied or closed, scrape the JD,
score the rest, then tailor the ones worth applying to and put them in today's SUMMARY.

    python -m apply_agent.batch triage list.tsv     # offline checks → open check + JD scrape → score; writes batch/REPORT.md
    python -m apply_agent.batch tailor [--max 15]   # tailor + prepare the qualified ones, rewrite SUMMARY.md

list.tsv is pipe-separated: tier|company|title|location|claimed|url  (a header row is ignored). Tiers 待开放 and
暂不投入 are skipped, as are rows without a posting URL. Everything lives in runs/<today>/batch/ (triage.json is the
state: rerunning `triage` only works on rows that are not final yet). A JD you paste into batch/jd/<row#>.txt
(for pages that need a browser) overrides the scrape for that row.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from apply_agent.core import llm, summary
from apply_agent.core.config import DATA, ROOT, load_config, run_dir as default_run_dir
from apply_agent.core.models import Job, read_jobs, write_jobs
from apply_agent.core.state import SeenJobs, company_role_key, fingerprint
from apply_agent.discover import filters
from apply_agent.discover.normalize import html_to_text, parse_date
from apply_agent.discover.sources.base import Http, make_job
from apply_agent.prepare import run as preparer
from apply_agent.score.run import score
from apply_agent.tailor import run as optimizer
from apply_agent.tailor.customize import run_all
from apply_agent.tailor.inbox import Inbox

SKIP_TIERS = {"待开放", "暂不投入"}
CLOSED_MARKERS = re.compile(
    r"no longer (?:accepting|available|open)|position (?:has been|is) (?:filled|closed)|job (?:has been )?(?:closed|expired)|"
    r"this (?:job|posting|position) (?:is )?(?:no longer|has expired|has been)|not currently accepting|"
    r"job you are trying to apply for has been filled|page you are looking for doesn", re.I)
FINAL = {"skipped", "applied", "prepared", "closed", "filtered", "rejected", "qualified", "selected", "tailored", "failed"}
MIN_JD_CHARS = 400


# ---------------------------------------------------------------- URL → (source, token, id) offline
def parse_url(url: str, company: str, boards: dict[str, str]) -> dict:
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    q = parse_qs(u.query)
    if host.endswith("greenhouse.io") and "jobs" in parts and parts.index("jobs") + 1 < len(parts):
        return dict(source="greenhouse", token=parts[0], id=parts[parts.index("jobs") + 1])
    gh = (q.get("gh_jid") or [None])[0]
    if not gh:
        m = re.search(r"-(\d{7,})$", u.path)
        gh = m.group(1) if m and "databricks" in host else None
    if gh:
        return dict(source="greenhouse", token=boards.get(_norm(company)) or _norm(company), id=gh)
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return dict(source="ashby", token=parts[0], id=parts[1])
    if host.endswith("lever.co") and len(parts) >= 2:
        return dict(source="lever", token=parts[0], id=parts[1])
    if host == "apply.workable.com" and len(parts) >= 3 and parts[1] == "j":
        return dict(source="workable", token=parts[0], id=parts[2])
    if host.endswith("myworkdayjobs.com") and "job" in parts:
        i = parts.index("job")
        site = [p for p in parts[:i] if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        req = re.split(r"[_-]", parts[-1])[-1] if "_" in parts[-1] else parts[-1]
        return dict(source="workday", token=host.split(".")[0], id=req, host=host, site=site[0] if site else "",
                    path="/" + "/".join(parts[i:]))
    if host.endswith("ycombinator.com") and "jobs" in parts:
        return dict(source="yc", token=parts[parts.index("companies") + 1], id=parts[-1].split("-")[0])
    return dict(source="manual", token="", id=hashlib.sha1(url.encode()).hexdigest()[:10])


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def job_id(p: dict) -> str:
    return f"{p['source']}:{p['token']}:{p['id']}" if p["source"] != "manual" else f"manual:{p['id']}"


# ---------------------------------------------------------------- already applied?
def seen_status(seen: SeenJobs, row: dict, p: dict) -> tuple[str, str] | None:
    """('applied'|'prepared', why) if this posting (or, for old records, the company's PM role) is already handled."""
    jid, fp = job_id(p), fingerprint(row["company"], row["title"])
    url = row["url"].split("?")[0].rstrip("/").lower()
    for k, e in seen.jobs.items():
        st = e.get("status")
        if st not in ("applied", "customized", "closed", "expired", "rejected"):
            continue
        same_url = bool(e.get("url")) and e["url"].split("?")[0].rstrip("/").lower() == url
        if k == jid or same_url or (st in ("applied", "customized") and e.get("fingerprint") == fp):
            kind = {"applied": "applied", "customized": "prepared"}.get(st)
            if kind:
                return kind, f"{st} · {e.get('resume_dir') or k}"
            return "closed" if st in ("closed", "expired") else "rejected", f"earlier run: {st} (score {e.get('match_score')})"
    c = _norm(row["company"])
    for k, e in seen.jobs.items():  # pre-pipeline folders know only company + role, so this is a coarser match
        if e.get("legacy") and e.get("status") in ("applied", "customized") and e["legacy_key"].endswith("|PM"):
            lc = e["legacy_key"].split("|")[0]
            if len(lc) >= 3 and (lc in c or c in lc):
                kind = "applied" if e["status"] == "applied" else "prepared"
                return kind, f"legacy {e['status']} PM record for {e.get('company')} (exact posting not recorded)"
    return None


# ---------------------------------------------------------------- fetch one posting
async def fetch(http: Http, row: dict, p: dict) -> tuple[Job | None, str]:
    """(job, '') if open; (None, reason) if closed / can't read. The reason starts 'closed:' or 'needs browser:'."""
    src, tok, pid = p["source"], p["token"], p["id"]
    title, locs, desc, url, posted, extra = row["title"], row["locations"], "", row["url"], None, {}
    try:
        if src == "greenhouse":
            r = await http.request("GET", f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{pid}")
            if r.status_code == 404:
                return None, "closed: not on the Greenhouse board any more (404)"
            r.raise_for_status()
            j = r.json()
            title, url = j["title"].strip(), j.get("absolute_url") or url
            locs = [(j.get("location") or {}).get("name") or ""] + [o["name"] for o in j.get("offices", [])]
            desc, posted = html_to_text(j.get("content")), parse_date(j.get("first_published") or j.get("updated_at"))
            extra = {"token": tok, "gh_id": int(pid)}
        elif src == "ashby":
            data = await http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}",
                                       params={"includeCompensation": "true"})
            j = next((x for x in data.get("jobs", []) if x["id"] == pid and x.get("isListed") is not False), None)
            if not j:
                return None, "closed: no longer listed on the Ashby board"
            title, url = j["title"].strip(), j["jobUrl"]
            locs = [x for x in [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []] if x]
            if j.get("isRemote"):
                locs.append("Remote - US")
            desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml"))
            posted, extra = parse_date(j.get("publishedAt")), {"token": tok}
        elif src == "lever":
            r = await http.request("GET", f"https://api.lever.co/v0/postings/{tok}/{pid}")
            if r.status_code == 404:
                return None, "closed: not on Lever (404)"
            r.raise_for_status()
            j = r.json()
            cats = j.get("categories") or {}
            title, url = (j.get("text") or title).strip(), j.get("hostedUrl") or url
            locs = list(cats.get("allLocations") or ([cats["location"]] if cats.get("location") else []))
            parts = [j.get("descriptionPlain") or ""] + [f"{l.get('text', '')}\n{html_to_text(l.get('content'))}"
                                                        for l in j.get("lists") or []] + [j.get("additionalPlain") or ""]
            desc, posted = "\n\n".join(x for x in parts if x).strip(), parse_date(j.get("createdAt"))
            extra = {"token": tok, "lever_id": pid, "apply_url": j.get("applyUrl")}
        elif src == "workable":
            r = await http.request("GET", f"https://apply.workable.com/api/v2/accounts/{tok}/jobs/{pid}")
            if r.status_code == 404:
                return None, "closed: not on Workable (404)"
            r.raise_for_status()
            j = r.json()
            title = (j.get("title") or title).strip()
            loc = j.get("location") or {}
            locs = [", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)]
            desc = "\n\n".join(html_to_text(j.get(k)) for k in ("description", "requirements", "benefits") if j.get(k))
            posted = parse_date(j.get("published"))
        elif src == "workday":
            r = await http.request("GET", f"https://{p['host']}/wday/cxs/{p['token']}/{p['site']}{p['path']}")
            if r.status_code in (404, 422):
                return None, "closed: Workday posting not found"
            r.raise_for_status()
            info = r.json().get("jobPostingInfo") or {}
            title, url = (info.get("title") or title).strip(), info.get("externalUrl") or url
            locs = [x for x in [info.get("location")] + list(info.get("additionalLocations") or []) if x]
            desc, posted = html_to_text(info.get("jobDescription")), parse_date(info.get("startDate"))
            if info.get("canApply") is False:
                return None, "closed: Workday says it can't be applied to"
            extra = {k: p[k] for k in ("host", "site", "path")} | {"tenant": p["token"]}
        else:  # yc, manual: read the page itself
            r = await http.request("GET", url, headers={"Accept": "text/html"})
            if r.status_code in (404, 410):
                return None, f"closed: page returns {r.status_code}"
            if r.status_code >= 400:
                return None, f"needs browser: HTTP {r.status_code}"
            html = r.text
            desc = _jsonld_description(html) or html_to_text(
                str(BeautifulSoup(html, "html.parser").find("main") or BeautifulSoup(html, "html.parser").body or ""))
            if CLOSED_MARKERS.search(desc[:3000]):
                return None, "closed: page says the role is no longer available"
    except Exception as e:
        return None, f"needs browser: {type(e).__name__}: {str(e)[:120]}"
    if len(desc) < MIN_JD_CHARS:
        return None, f"needs browser: only {len(desc)} chars of JD scraped"
    if CLOSED_MARKERS.search(desc[:1500]):
        return None, "closed: posting text says it is no longer open"
    locs = [l for l in dict.fromkeys(l.strip() for l in locs if l and l.strip())]
    job = make_job(id=job_id(p), source=src if src in ("greenhouse", "lever", "ashby", "workday") else "manual",
                   company=row["company"], title=title, role_type="PM", locations=locs or row["locations"],
                   url=url, posted_at=posted, description=desc, extra=extra)
    return job, ""


def _jsonld_description(html: str) -> str:
    for tag in BeautifulSoup(html, "html.parser").find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") == "JobPosting" and d.get("description"):
                return html_to_text(d["description"])
    return ""


# ---------------------------------------------------------------- stages
def read_list(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        f = [x.strip() for x in line.split("|")]
        if i == 0 and f[0] == "tier" or len(f) < 6 or not f[0]:
            continue
        rows.append(dict(n=len(rows) + 1, tier=f[0], company=f[1], title=f[2],
                         locations=[l.strip() for l in re.split(r"[;/]", f[3]) if l.strip()], claimed=f[4], url=f[5],
                         status="new", reason=""))
    return rows


def boards_by_company() -> dict[str, str]:
    path = ROOT / "apply_agent" / "discover" / "companies.yaml"
    return {_norm(c["name"]): c["token"] for c in yaml.safe_load(path.read_text()) if c.get("ats") == "greenhouse"}


async def triage(rows: list[dict], cfg, bd: Path) -> None:
    seen, boards, http = SeenJobs(), boards_by_company(), Http()
    # A + B: offline
    todo = []
    for r in rows:
        if r["status"] in FINAL:
            continue
        if r["tier"] in SKIP_TIERS:
            r.update(status="skipped", reason=f"tier {r['tier']} (not open yet / you parked it)")
        elif not r["url"].startswith("http") or r["url"].rstrip("/").count("/") < 3:
            r.update(status="skipped", reason="no direct posting link")
        else:
            r["p"] = parse_url(r["url"], r["company"], boards)
            hit = seen_status(seen, r, r["p"])
            if hit:
                r.update(status=hit[0], reason=hit[1])
            else:
                todo.append(r)
    # C: open check + JD
    async def one(r):
        override = bd / "jd" / f"{r['n']}.txt"
        if override.exists():
            r["job"] = make_job(id=job_id(r["p"]), source="manual", company=r["company"], title=r["title"], role_type="PM",
                                locations=r["locations"], url=r["url"], description=override.read_text("utf-8").strip(),
                                extra={"token": r["p"]["token"]} if r["p"]["source"] == "greenhouse" else {}).model_dump(mode="json")
            return
        job, why = await fetch(http, r, r["p"])
        if job is None:
            r.update(status="closed" if why.startswith("closed") else "needs_browser", reason=why)
        else:
            r["job"] = job.model_dump(mode="json")
    await asyncio.gather(*(one(r) for r in todo))
    await http.aclose()
    # D: hard checks, then LLM score
    llm.USAGE.budget_usd = cfg.daily_budget_usd
    scoring = []
    for r in todo:
        if "job" not in r:
            continue
        job = Job.model_validate(r["job"])
        m = filters._NO_SPONSOR.search(job.description)
        yrs = filters.min_years(job.description, getattr(cfg.search, "degree_path", True))
        if filters.too_old(job, cfg.search.max_age_days_manual):
            r.update(status="filtered", reason=f"posted over {cfg.search.max_age_days_manual} days ago")
        elif m:
            r.update(status="filtered", reason=f"no sponsorship / clearance: “{m.group(0)}”")
        elif yrs is not None and yrs > cfg.search.max_years_required:
            r.update(status="filtered", reason=f"requires {yrs}+ years")
        else:
            scoring.append((r, job))
    res = await llm.gather_limited([score(j, cfg) for _, j in scoring], cfg.matchmaker.concurrency)
    for (r, _), out in zip(scoring, res):
        if isinstance(out, Exception):
            r.update(status="failed", reason=f"scoring error: {out}")
            continue
        r["job"] = out.model_dump(mode="json")
        ok = bool(out.meets_needs) and (out.match_score or 0) >= cfg.matchmaker.min_score
        r.update(score=out.match_score, aligned=out.alignment, gaps=out.gaps,
                 status="qualified" if ok else "rejected",
                 reason="" if ok else (out.needs_reasons or f"score {out.match_score} < {cfg.matchmaker.min_score}"))


def pick(rows: list[dict], cfg, limit: int) -> list[dict]:
    """Best first; at most max_per_company per company (1 for TikTok: it caps you at two applications company-wide)."""
    cap = {"tiktok": 1}
    per: dict[str, int] = {}
    chosen = []
    for r in sorted((r for r in rows if r["status"] == "qualified"), key=lambda r: -(r.get("score") or 0)):
        c = _norm(r["company"])
        if per.get(c, 0) >= cap.get(c, cfg.rank.max_per_company):
            r["note"] = "over the per-company cap"
            continue
        if len(chosen) >= limit:
            r["note"] = "over --max"
            continue
        per[c] = per.get(c, 0) + 1
        chosen.append(r)
    return chosen


LABEL = {"applied": "Already applied (skipped)", "prepared": "Already prepared in your backlog (skipped)",
         "closed": "Closed / not found", "needs_browser": "Could not read the page (needs a browser or paste)",
         "filtered": "Filtered by hard checks", "rejected": "Scored but below the bar / failed needs",
         "skipped": "Skipped without checking", "failed": "Errors", "qualified": "Worth applying", "selected": "Worth applying",
         "tailored": "Tailored"}


def report(rows: list[dict]) -> str:
    out = ["# Batch triage", ""]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out.append(" · ".join(f"{LABEL.get(k, k)}: {v}" for k, v in counts.items()) + "\n")
    for st in ("qualified", "selected", "tailored", "rejected", "filtered", "closed", "needs_browser", "applied", "prepared",
               "skipped", "failed"):
        grp = [r for r in rows if r["status"] == st]
        if not grp:
            continue
        out.append(f"## {LABEL[st]} ({len(grp)})")
        for r in sorted(grp, key=lambda r: -(r.get("score") or 0)):
            s = f" · match {r['score']}" if r.get("score") is not None else ""
            loc = "; ".join((r.get("job") or {}).get("locations") or r["locations"])
            out.append(f"- **{r['company']}** · {r['title']}{s} · {loc} — {r['reason'] or r.get('note', '')}\n  {r['url']}")
        out.append("")
    return "\n".join(out)


def save(rows: list[dict], bd: Path) -> None:
    (bd / "triage.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    (bd / "REPORT.md").write_text(report(rows), encoding="utf-8")


# ---------------------------------------------------------------- tailor + summary
def tailor(rows: list[dict], cfg, rd: Path, limit: int) -> None:
    chosen = pick(rows, cfg, limit)
    if not chosen:
        print("nothing qualified")
        return
    jobs = [Job.model_validate(r["job"]) for r in chosen]
    print(f"… tailoring {len(jobs)} resumes (concurrency {cfg.customize.concurrency}, a few minutes each)")
    done = asyncio.run(run_all(jobs, cfg, rd))
    optimizer._record(done, rd, cfg)
    top = rd / "03_top.json"
    prev = read_jobs(top)
    nxt = max([j.rank or 0 for j in prev], default=0)
    for r, j in zip(chosen, done):
        r["status"], r["reason"] = "tailored", f"{j.customize_status}: {j.resume_pdf}"
        if j.customize_status != "failed":
            nxt += 1
            j.rank = nxt
    keep = {j.id for j in done}
    write_jobs(top, [j for j in prev if j.id not in keep] + done)
    preparer.main(rd, cfg, no_sheet=True)
    summary.write(rd, spent=llm.USAGE.usd, n_open_questions=len(Inbox().open), closed=[], failed_step=None)
    add_summary_section(rd, rows)


MARK = "<!-- batch-triage -->"


def add_summary_section(rd: Path, rows: list[dict]) -> None:
    p = rd / "SUMMARY.md"
    text = p.read_text(encoding="utf-8").split(MARK)[0].rstrip()
    sec = [MARK, "", "## From your pasted list (triage)", "",
           "Tailored ones are in **New today** above. The rest of the list:", ""]
    for st in ("qualified", "prepared", "applied", "rejected", "filtered", "closed", "needs_browser", "skipped"):
        grp = [r for r in rows if r["status"] == st]
        if not grp:
            continue
        label = LABEL[st] if st != "qualified" else "Worth applying but not tailored (cap)"
        sec.append(f"**{label} ({len(grp)})**")
        sec += [f"- {r['company']} · {r['title']}" + (f" · match {r['score']}" if r.get("score") is not None else "")
                + f" — {r['reason'] or r.get('note', '')}" for r in grp]
        sec.append("")
    p.write_text(text + "\n\n" + "\n".join(sec), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("triage")
    t.add_argument("list", type=Path)
    t.add_argument("--refresh", action="store_true", help="redo rows that already have a final status")
    k = sub.add_parser("tailor")
    k.add_argument("--max", type=int, default=15)
    a = ap.parse_args()
    cfg = load_config()
    if (DATA / "run.lock").exists():
        print("✗ a pipeline run is in progress")
        return 1
    rd = default_run_dir(cfg)
    bd = rd / "batch"
    (bd / "jd").mkdir(parents=True, exist_ok=True)
    if a.cmd == "triage":
        prev = {r["url"] + r["title"]: r for r in json.loads((bd / "triage.json").read_text())} \
            if (bd / "triage.json").exists() and not a.refresh else {}
        rows = [prev.get(r["url"] + r["title"], r) for r in read_list(a.list)]
        for r in rows:
            if r["status"] in ("needs_browser", "failed"):
                r["status"] = "new"  # retry these
        asyncio.run(triage(rows, cfg, bd))
        save(rows, bd)
        print((bd / "REPORT.md").read_text(encoding="utf-8"))
        print(f"spent ${llm.USAGE.usd:.3f}")
    else:
        rows = json.loads((bd / "triage.json").read_text())
        tailor(rows, cfg, rd, a.max)
        save(rows, bd)
        print(f"✓ SUMMARY updated: {rd / 'SUMMARY.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
