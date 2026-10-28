"""Greenhouse, Lever, Ashby, SmartRecruiters and Workday sources (APIs verified 2026-10-03)."""
from __future__ import annotations

import re

from apply_agent.core.models import Job

from ..normalize import html_to_text, parse_date
from .base import Http, Source, make_job

SWE_QUERIES = ["software engineer", "software developer", "machine learning engineer", "ai engineer"]
PM_QUERIES = ["product manager"]


def _split_locs(s: str | None) -> list[str]:
    if not s:
        return []
    parts = re.split(r";|\s+\|\s+|\s+or\s+|\s*/\s*(?=[A-Z])", s)
    return [p.strip() for p in parts if p.strip()]


class Greenhouse(Source):
    name = "greenhouse"
    BASE = "https://boards-api.greenhouse.io/v1/boards"

    async def list_jobs(self, http: Http, company: dict) -> list[Job]:
        token = company["token"]
        data = await http.get_json(f"{self.BASE}/{token}/jobs", params={"content": "true"})
        jobs = []
        for j in data.get("jobs", []):
            locs = _split_locs((j.get("location") or {}).get("name"))
            locs += [o["name"] for o in j.get("offices", []) if o.get("name") and o["name"] not in locs]
            jobs.append(make_job(
                id=f"greenhouse:{token}:{j['id']}", source=self.name,
                company=company["name"], title=j["title"].strip(), locations=locs,
                url=j["absolute_url"], posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
                description=html_to_text(j.get("content")), extra={"token": token, "gh_id": j["id"]},
            ))
        return jobs


class Lever(Source):
    name = "lever"

    async def list_jobs(self, http: Http, company: dict) -> list[Job]:
        token = company["token"]
        host = company.get("host", "api.lever.co")
        r = await http.request("GET", f"https://{host}/v0/postings/{token}", params={"mode": "json"})
        if r.status_code == 404 and host == "api.lever.co":
            r = await http.request("GET", f"https://api.eu.lever.co/v0/postings/{token}", params={"mode": "json"})
        r.raise_for_status()
        jobs = []
        for j in r.json():
            cats = j.get("categories") or {}
            if "intern" in (cats.get("commitment") or "").lower():
                title_suffix = " (Intern)"
            else:
                title_suffix = ""
            locs = list(cats.get("allLocations") or ([cats["location"]] if cats.get("location") else []))
            if (j.get("workplaceType") or "").lower() == "remote":
                locs.append(f"Remote - {j.get('country') or ''}".strip(" -"))
            parts = [j.get("descriptionPlain") or ""]
            for lst in j.get("lists") or []:
                parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
            parts.append(j.get("additionalPlain") or "")
            jobs.append(make_job(
                id=f"lever:{token}:{j['id']}", source=self.name, company=company["name"],
                title=(j.get("text") or "").strip() + title_suffix, locations=locs, url=j["hostedUrl"],
                posted_at=parse_date(j.get("createdAt")), description="\n\n".join(p for p in parts if p).strip(),
                extra={"token": token, "lever_id": j["id"], "apply_url": j.get("applyUrl")},
            ))
        return jobs


class Ashby(Source):
    name = "ashby"

    async def list_jobs(self, http: Http, company: dict) -> list[Job]:
        token = company["token"]
        data = await http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}",
                                   params={"includeCompensation": "true"})
        jobs = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j["location"]] if j.get("location") else []
            locs += [s.get("location") for s in j.get("secondaryLocations") or [] if s.get("location")]
            if j.get("isRemote"):
                country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
                locs.append(f"Remote - {country}".strip(" -"))
            title = j["title"].strip()
            if (j.get("employmentType") or "").lower() == "intern" and "intern" not in title.lower():
                title += " (Intern)"
            jobs.append(make_job(
                id=f"ashby:{token}:{j['id']}", source=self.name, company=company["name"], title=title,
                locations=locs, url=j["jobUrl"], posted_at=parse_date(j.get("publishedAt")),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                extra={"token": token},
            ))
        return jobs


class SmartRecruiters(Source):
    name = "smartrecruiters"
    BASE = "https://api.smartrecruiters.com/v1/companies"

    async def list_jobs(self, http: Http, company: dict) -> list[Job]:
        token = company["token"]
        seen: dict[str, Job] = {}
        for q in SWE_QUERIES + PM_QUERIES:
            offset = 0
            while True:
                data = await http.get_json(f"{self.BASE}/{token}/postings",
                                           params={"q": q, "country": "us", "limit": 100, "offset": offset})
                for j in data.get("content", []):
                    if j["id"] in seen:
                        continue
                    loc = j.get("location") or {}
                    locs = [loc.get("fullLocation") or ", ".join(
                        x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)]
                    if loc.get("remote"):
                        locs.append(f"Remote - {(loc.get('country') or '').upper()}".strip(" -"))
                    seen[j["id"]] = make_job(
                        id=f"smartrecruiters:{token}:{j['id']}", source=self.name, company=company["name"],
                        title=j["name"].strip(), locations=[l for l in locs if l],
                        url=f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                        posted_at=parse_date(j.get("releasedDate")),
                        extra={"token": token, "experience_level": (j.get("experienceLevel") or {}).get("id")},
                    )
                offset += 100
                if offset >= data.get("totalFound", 0) or offset >= 500:
                    break
        return list(seen.values())

    async def fetch_detail(self, http: Http, job: Job) -> Job:
        d = await http.get_json(f"{self.BASE}/{job.extra['token']}/postings/{job.posting_id}")
        sections = ((d.get("jobAd") or {}).get("sections")) or {}
        job.description = "\n\n".join(
            html_to_text((sections.get(k) or {}).get("text"))
            for k in ("jobDescription", "qualifications", "additionalInformation")).strip()
        job.url = d.get("postingUrl") or job.url
        return job


class Workday(Source):
    """Undocumented CXS endpoint used by every myworkdayjobs.com site. Expect breakage."""
    name = "workday"

    def _base(self, c: dict) -> str:
        return f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"

    async def list_jobs(self, http: Http, company: dict) -> list[Job]:
        seen: dict[str, Job] = {}
        for q in SWE_QUERIES + PM_QUERIES:
            offset = 0
            while True:
                data = await http.post_json(f"{self._base(company)}/jobs",
                                            {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
                posts = data.get("jobPostings", [])
                for j in posts:
                    path = j.get("externalPath") or ""
                    req = (j.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0]
                    if req in seen or not path:
                        continue
                    lt = j.get("locationsText") or ""
                    seen[req] = make_job(
                        id=f"workday:{company['tenant']}:{req}", source=self.name, company=company["name"],
                        title=(j.get("title") or "").strip(),
                        locations=[] if re.match(r"\d+\s+Locations", lt) else [lt],
                        url=f"https://{company['host']}/en-US/{company['site']}{path}",
                        posted_at=parse_date(j.get("postedOn")),
                        extra={**{k: company[k] for k in ("host", "tenant", "site")}, "path": path},
                    )
                offset += 20
                if not posts or offset >= min(data.get("total", 0), 200):
                    break
        return list(seen.values())

    async def fetch_detail(self, http: Http, job: Job) -> Job:
        d = await http.get_json(f"{self._base(job.extra)}{job.extra['path']}")
        info = d.get("jobPostingInfo") or {}
        job.description = html_to_text(info.get("jobDescription"))
        locs = [info.get("location")] + list(info.get("additionalLocations") or [])
        job.locations = [l for l in locs if l] or job.locations
        job.posted_at = parse_date(info.get("startDate")) or job.posted_at
        job.url = info.get("externalUrl") or job.url
        return job


SOURCES: dict[str, Source] = {s.name: s for s in (Greenhouse(), Lever(), Ashby(), SmartRecruiters(), Workday())}
