"""data/seen_jobs.json: the pipeline's memory, so no job is scored, tailored or shown twice."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DATA

SEEN_PATH = DATA / "seen_jobs.json"
STATUSES = {"rejected", "matched", "customized", "applied", "closed", "expired", "failed"}
DONE = {"customized", "applied", "closed", "expired"}  # never tailor these again
FINGERPRINT_WINDOW_DAYS = 60

_NOISE = re.compile(
    r"\b(20\d\d|new\s*grad(uate)?s?|new\s*college\s*grad(uate)?|university\s*grad(uate)?|early\s*career|"
    r"entry[\s-]*level|remote|hybrid|onsite|united\s*states|usa?|us)\b", re.I)


def fingerprint(company: str, title: str) -> str:
    t = title.lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)          # (2027), [Remote]
    t = _NOISE.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = " ".join(t.split())
    c = re.sub(r"[^a-z0-9]+", "", company.lower())
    return f"{c}|{t}"


def company_role_key(company: str, role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", company.lower()) + "|" + role.upper()


class SeenJobs:
    def __init__(self, path: Path = SEEN_PATH):
        self.path = path
        self.jobs: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}

    # ---- queries ----
    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def _fp_recent(self, fp: str, statuses: set[str] | None = None) -> dict | None:
        cutoff = (date.today() - timedelta(days=FINGERPRINT_WINDOW_DAYS)).isoformat()
        for entry in self.jobs.values():
            if entry.get("fingerprint") == fp and entry.get("updated", "") >= cutoff:
                if statuses is None or entry.get("status") in statuses:
                    return entry
        return None

    def is_seen(self, job_id: str, fp: str) -> bool:
        """True if this exact posting, or a recent repost of it, was already processed."""
        e = self.jobs.get(job_id)
        if e:
            return not (e.get("status") == "failed" and e.get("retries", 0) < 1)
        return self._fp_recent(fp) is not None

    def is_done(self, job_id: str, fp: str) -> bool:
        e = self.jobs.get(job_id)
        if e and e.get("status") in DONE:
            return True
        return self._fp_recent(fp, DONE) is not None

    def legacy_block(self, company: str, role: str | None) -> bool:
        """Folders that existed before the pipeline (no posting id) block that company+role for 60 days."""
        key = company_role_key(company, role or "")
        cutoff = (date.today() - timedelta(days=FINGERPRINT_WINDOW_DAYS)).isoformat()
        return any(e.get("legacy_key") == key and e.get("updated", "") >= cutoff for e in self.jobs.values())

    def by_status(self, *statuses: str) -> dict[str, dict]:
        return {k: v for k, v in self.jobs.items() if v.get("status") in statuses}

    # ---- updates ----
    def set(self, job_id: str, status: str, **fields) -> None:
        assert status in STATUSES, status
        now = date.today().isoformat()
        e = self.jobs.setdefault(job_id, {"first_seen": now})
        if status == "failed" and e.get("status") == "failed":
            e["retries"] = e.get("retries", 0) + 1
        e.update(fields)
        e["status"] = status
        e["updated"] = now

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.jobs, indent=2, ensure_ascii=False, sort_keys=True))
        tmp.replace(self.path)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
