"""Shared HTTP client (per-host concurrency, retries) and the Source interface."""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from urllib.parse import urlparse

import httpx

from apply_agent.core.models import Job
from apply_agent.core.state import fingerprint


def user_agent() -> str:
    """Names the tool and, when a profile exists, a contact address so a site owner can reach whoever is fetching."""
    from apply_agent.core.profile import ProfileError, load_profile
    try:
        contact = f"; contact: {load_profile().contact.email}"
    except ProfileError:
        contact = ""
    return f"apply_agent/1.0 (personal job search{contact})"


class Http:
    def __init__(self, per_host: int = 5, timeout: float = 25.0):
        self.client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": user_agent()}, follow_redirects=True)
        self._sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))

    @staticmethod
    def _limit_key(url: str) -> str:
        """Workday rate-limits per data center (wd1, wd5, …), not per company, so share one limit per cluster."""
        host = urlparse(url).netloc
        m = re.search(r"(wd\d+\.myworkdayjobs\.com)$", host)
        return m.group(1) if m else host

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        key = self._limit_key(url)
        if key not in self._sems and key.endswith("myworkdayjobs.com"):
            self._sems[key] = asyncio.Semaphore(2)
        sem = self._sems[key]
        delay = 2.0
        for attempt in range(6):
            async with sem:
                try:
                    r = await self.client.request(method, url, **kw)
                except httpx.TransportError:
                    if attempt == 5:
                        raise
                    r = None
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                return r
            retry_after = r.headers.get("retry-after") if r is not None else None
            await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
            delay = min(delay * 2, 30)
        return r  # type: ignore[return-value]

    async def get_json(self, url: str, **kw):
        r = await self.request("GET", url, **kw)
        r.raise_for_status()
        return r.json()

    async def post_json(self, url: str, body: dict, **kw):
        r = await self.request("POST", url, json=body, **kw)
        r.raise_for_status()
        return r.json()

    async def aclose(self):
        await self.client.aclose()


def make_job(**kw) -> Job:
    job = Job(**kw)
    job.fingerprint = fingerprint(job.company, job.title)
    return job


class Source:
    """One ATS. `list_jobs` returns normalized jobs (description may be empty);
    `fetch_detail` fills description/posted_at/locations for jobs that survived the cheap filters."""

    name = "base"

    async def list_jobs(self, http: Http, company: dict) -> list[Job]:
        raise NotImplementedError

    async def fetch_detail(self, http: Http, job: Job) -> Job:
        return job
