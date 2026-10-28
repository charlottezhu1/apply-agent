"""Rule-based filters. Each returns a drop reason (str) or None to keep. Pure functions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from apply_agent.core.models import Job

from .normalize import is_bay_area, is_remote_us, is_us

# ---------- role ----------
_NEVER = re.compile(
    r"product\s+marketing|project\s+manager|program\s+manager|engineering\s+manager|sales\s+engineer|"
    r"solutions?\s+engineer|support\s+engineer|customer\s+engineer|test\s+engineer|\bqa\b|quality\s+assurance|"
    r"hardware|firmware|mechanical|electrical|manufacturing|recruit|account\s+(executive|manager)|"
    r"designer|data\s+analyst|business\s+analyst|technician|counsel|attorney|\bsdet\b|"
    r"network\s+engineer|it\s+engineer|security\s+analyst|reliability\s+engineer\s+-\s+hardware", re.I)
_PM = re.compile(
    r"\bproduct\s+manager\b|\bassociate\s+product\s+manager\b|\bapm\b|\brotational\s+product|"
    r"\btechnical\s+product\s+manager\b|\bproduct\s+management\b", re.I)
_SWE = re.compile(
    r"software\s+(engineer|developer|development\s+engineer)|\bswe\b|full[\s-]*stack|back[\s-]*end|"
    r"front[\s-]*end|machine\s+learning\s+engineer|\bml\s+engineer|\bai\s+engineer|applied\s+ai|"
    r"\bai/ml\b|forward\s+deployed\s+engineer|founding\s+engineer|product\s+engineer|mobile\s+engineer|"
    r"\bios\b.*engineer|android.*engineer|platform\s+engineer|infrastructure\s+engineer|"
    r"\bmember\s+of\s+technical\s+staff\b|\bmts\b|web\s+(developer|engineer)|developer\b|"
    r"\bengineer\b.*\b(new\s*grad|university|early\s*career|entry)", re.I)

# ---------- seniority ----------
_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|head\s+of|vp|vice\s+president|distinguished|"
    r"architect|fellow|chief|group\s+product\s+manager|manager,?\s+(software|engineering))\b|"
    r"\b(ii|iii|iv|v)\b|\b(engineer|developer|manager)\s+[2-9]\b|\bl[4-9]\b|\blevel\s+[3-9]\b|"
    r"\b(smts|lmts|pmts)\b", re.I)
_MANAGER = re.compile(r"\bmanager\b", re.I)
_INTERN = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b|\bapprentice", re.I)

# ---------- years ----------
_YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)(?:'|’)?\s*(?:of)?\s*"
    r"(?:\w+\s+){0,6}?(?:experience|exp\b)", re.I)
_YEARS_ALT = re.compile(r"(?:minimum|at\s+least)\s+(?:of\s+)?(\d{1,2})\s+(?:years?|yrs?)", re.I)
# "3+ years, or 1+ with a Master's": counts only when search.degree_path is on (the applicant has a graduate degree).
_YEARS_DEGREE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)?\s*(?:of\s+(?:\w+\s+){0,3}experience\s+)?"
    r"(?:with|and|plus|given|if\s+you\s+have)\s+(?:an?\s+)?(?:master|m\.?s\.?\b|ph\.?d|graduate\s+degree)", re.I)

# ---------- sponsorship ----------
_NO_SPONSOR = re.compile(
    r"unable\s+to\s+(?:provide\s+)?(?:visa\s+)?sponsor|will\s+not\s+(?:provide\s+)?(?:visa\s+)?sponsor|"
    r"cannot\s+(?:provide\s+)?(?:visa\s+)?sponsor|can\s*not\s+sponsor|not\s+able\s+to\s+sponsor|"
    r"does\s+not\s+(?:provide|offer)\s+(?:visa\s+)?sponsorship|no\s+(?:visa\s+)?sponsorship|"
    r"sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)|not\s+eligible\s+for\s+(?:visa\s+)?sponsorship|"
    r"without\s+(?:the\s+need\s+for\s+)?(?:current\s+or\s+future\s+|future\s+)?(?:visa\s+|employer\s+)?sponsorship|"
    r"must\s+be\s+(?:a\s+)?u\.?s\.?\s+citizen|u\.?s\.?\s+citizenship\s+(?:is\s+)?required|"
    r"(?:active|current)\s+(?:secret|top\s+secret|ts/sci)?\s*(?:security\s+)?clearance|"
    r"security\s+clearance\s+(?:is\s+)?required|green\s+card\s+holders?\s+only|"
    r"only\s+u\.?s\.?\s+citizens", re.I)
_YES_SPONSOR = re.compile(
    r"visa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)|will\s+sponsor|we\s+(?:do\s+)?sponsor|"
    r"sponsorship\s+(?:is\s+)?available|h-?1b\s+sponsorship\s+(?:is\s+)?available|open\s+to\s+sponsor", re.I)


def classify_role(title: str) -> str | None:
    if _NEVER.search(title):
        return None
    if _PM.search(title):
        return "PM"
    if _SWE.search(title):
        return "SWE"
    return None


def role_filter(job: Job, cfg) -> str | None:
    role = classify_role(job.title)
    if role is None or role not in cfg.search.roles:
        return "role"
    job.role_type = role
    return None


def seniority_filter(job: Job, cfg) -> str | None:
    t = job.title
    if _SENIOR.search(t):
        return "seniority"
    # "Manager" alone is senior unless it's a PM title
    if _MANAGER.search(t) and job.role_type != "PM":
        return "seniority"
    return None


def intern_filter(job: Job, cfg) -> str | None:
    if not cfg.search.include_internships and _INTERN.search(job.title):
        return "internship"
    return None


def classify_locations(job: Job) -> None:
    locs = job.locations or []
    job.bay_area = any(is_bay_area(l) for l in locs)
    job.remote_us = any(is_remote_us(l) for l in locs)


def location_filter(job: Job, cfg) -> str | None:
    classify_locations(job)
    mode = cfg.search.location_mode
    if not job.locations:
        return None  # unknown: let the matchmaker read the JD
    if mode == "bay_area_only":
        ok = job.bay_area
    elif mode == "bay_area_or_remote":
        ok = job.bay_area or job.remote_us
    else:
        ok = any(is_us(l) for l in job.locations)
    return None if ok else "location"


def too_old(job: Job, max_days: int, now: datetime | None = None) -> bool:
    if job.posted_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return job.posted_at < now - timedelta(days=max_days)


def age_filter(job: Job, cfg, now: datetime | None = None) -> str | None:
    return "age" if too_old(job, cfg.search.max_age_days, now) else None


def min_years(text: str, degree_path: bool = True) -> int | None:
    """Smallest minimum years-of-experience stated in the JD ('3+ years, or 1+ with a Master's' → 1,
    or 3 when degree_path is off)."""
    vals = [int(m.group(1)) for m in _YEARS.finditer(text)]
    vals += [int(m.group(1)) for m in _YEARS_ALT.finditer(text)]
    if degree_path:
        vals += [int(m.group(1)) for m in _YEARS_DEGREE.finditer(text)]
    vals = [v for v in vals if v <= 20]
    return min(vals) if vals else None


def years_filter(job: Job, cfg) -> str | None:
    job.years_required_min = min_years(job.description, getattr(cfg.search, "degree_path", True))
    if job.years_required_min is not None and job.years_required_min > cfg.search.max_years_required:
        return "years"
    return None


def sponsorship_signal(text: str) -> str:
    if _NO_SPONSOR.search(text):
        return "no"
    if _YES_SPONSOR.search(text):
        return "yes"
    return "unknown"


def sponsorship_filter(job: Job, cfg) -> str | None:
    if job.sponsorship_signal == "unknown":
        job.sponsorship_signal = sponsorship_signal(job.description)  # type: ignore[assignment]
    return "sponsorship" if job.sponsorship_signal == "no" else None


# Filters that only need title/location/date run BEFORE fetching the JD.
PRE_FETCH = [role_filter, seniority_filter, intern_filter, location_filter, age_filter]
# Location runs again after the detail fetch: Workday lists say "3 Locations" until then.
POST_FETCH = [location_filter, age_filter, years_filter, sponsorship_filter]
