"""SimplifyJobs New-Grad feed: ~20k community-maintained postings across all ATSs.
Used as a job source and to discover new companies (see discover.py)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from apply_agent.core.models import Job

from ..normalize import html_to_text, parse_date
from .base import Http, Source, make_job

FEED = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
NO_SPONSOR = {"Does Not Offer Sponsorship", "U.S. Citizenship is Required"}


def parse_ats_url(url: str) -> dict | None:
    """Map a posting URL to {ats, token, posting_id, ...}; None if not a supported ATS."""
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)
    if "greenhouse.io" in host:
        if "for" in qs and "token" in qs:  # embed/job_app?for=x&token=123
            return {"ats": "greenhouse", "token": qs["for"][0], "posting_id": qs["token"][0]}
        if parts and parts[0] == "embed":
            return {"ats": "greenhouse", "token": None, "posting_id": (qs.get("token") or [None])[0]}
        if len(parts) >= 3 and parts[1] == "jobs":
            return {"ats": "greenhouse", "token": parts[0], "posting_id": parts[2]}
        if parts:
            return {"ats": "greenhouse", "token": parts[0], "posting_id": None}
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return {"ats": "lever", "token": parts[0], "posting_id": parts[1] if len(parts) > 1 else None}
    if host == "jobs.ashbyhq.com" and parts:
        return {"ats": "ashby", "token": parts[0], "posting_id": parts[1] if len(parts) > 1 else None}
    if host == "jobs.smartrecruiters.com" and parts:
        pid = re.match(r"(\d+)", parts[1]).group(1) if len(parts) > 1 and re.match(r"\d+", parts[1]) else None
        return {"ats": "smartrecruiters", "token": parts[0], "posting_id": pid}
    m = re.match(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if m and parts:
        if re.match(r"^[a-z]{2}-[A-Z]{2}$", parts[0]):
            parts = parts[1:]
        if not parts:
            return None
        path = "/" + "/".join(parts[1:]) if len(parts) > 1 else ""
        return {"ats": "workday", "host": host, "tenant": m.group(1), "site": parts[0], "path": path,
                "posting_id": path.rsplit("_", 1)[-1] if "_" in path else None}
    if "gh_jid" in qs:  # company-hosted Greenhouse page; token unknown
        return {"ats": "greenhouse", "token": None, "posting_id": qs["gh_jid"][0]}
    return None


def ats_job_id(info: dict | None) -> str | None:
    """The id the matching ATS source would give this posting, for cross-source dedupe."""
    if not info or not info.get("posting_id"):
        return None
    if info["ats"] == "workday":
        return f"workday:{info['tenant']}:{info['posting_id']}"
    if not info.get("token"):
        return None
    return f"{info['ats']}:{info['token']}:{info['posting_id']}"


class Simplify(Source):
    name = "simplify"

    async def list_jobs(self, http: Http, company: dict | None = None) -> list[Job]:
        data = await http.get_json(FEED)
        jobs = []
        for it in data:
            if not it.get("active") or not it.get("is_visible", True):
                continue
            info = parse_ats_url(it.get("url") or "")
            jobs.append(make_job(
                id=f"simplify:feed:{it['id']}", source=self.name, company=it["company_name"].strip(),
                title=it["title"].strip(), locations=it.get("locations") or [], url=it["url"],
                posted_at=parse_date(it.get("date_posted")),
                sponsorship_signal="no" if it.get("sponsorship") in NO_SPONSOR else "unknown",
                extra={"ats": info, "ats_id": ats_job_id(info), "sponsorship": it.get("sponsorship")},
            ))
        return jobs

    async def fetch_detail(self, http: Http, job: Job) -> Job:
        """Simplify has no JD; fetch it from the underlying ATS when we can."""
        info = job.extra.get("ats")
        if not info or not info.get("posting_id"):
            return job
        pid, tok = info["posting_id"], info.get("token")
        try:
            if info["ats"] == "greenhouse" and tok:
                d = await http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{pid}")
                job.description = html_to_text(d.get("content"))
            elif info["ats"] == "lever" and tok:
                d = await http.get_json(f"https://api.lever.co/v0/postings/{tok}/{pid}")
                parts = [d.get("descriptionPlain") or ""]
                parts += [f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in d.get("lists") or []]
                parts.append(d.get("additionalPlain") or "")
                job.description = "\n\n".join(p for p in parts if p)
            elif info["ats"] == "ashby" and tok:
                d = await http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
                hit = next((j for j in d.get("jobs", []) if j.get("id") == pid), None)
                if hit:
                    job.description = hit.get("descriptionPlain") or html_to_text(hit.get("descriptionHtml"))
            elif info["ats"] == "smartrecruiters" and tok:
                d = await http.get_json(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings/{pid}")
                s = ((d.get("jobAd") or {}).get("sections")) or {}
                job.description = "\n\n".join(html_to_text((s.get(k) or {}).get("text"))
                                              for k in ("jobDescription", "qualifications", "additionalInformation"))
            elif info["ats"] == "workday":
                d = await http.get_json(
                    f"https://{info['host']}/wday/cxs/{info['tenant']}/{info['site']}{info['path']}")
                job.description = html_to_text((d.get("jobPostingInfo") or {}).get("jobDescription"))
        except Exception:
            pass  # keep the job with an empty JD; the matchmaker caps its score
        return job
