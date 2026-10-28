from datetime import datetime, timedelta, timezone

import pytest

from apply_agent.core.models import Job
from apply_agent.discover import filters as F
from apply_agent.discover.normalize import html_to_text, is_bay_area, is_remote_us, parse_date
from apply_agent.discover.sources.simplify import ats_job_id, parse_ats_url


def job(title="Software Engineer", desc="", locs=("San Francisco, CA",), **kw):
    return Job(id="t:x:1", source="test", company="Acme", title=title, url="https://x", description=desc,
               locations=list(locs), **kw)


def run(fs, j, cfg):
    for f in fs:
        r = f(j, cfg)
        if r:
            return r
    return None


@pytest.mark.parametrize("title,expected", [
    ("Software Engineer, New Grad (2027)", None),
    ("Software Engineer I", None),
    ("Machine Learning Engineer - Product", None),
    ("Associate Product Manager", None),
    ("Senior Software Engineer", "seniority"),
    ("Software Engineer II", "seniority"),
    ("Staff Engineer, Infra", "role"),  # "engineer" alone isn't a SWE title → role
    ("Product Marketing Manager", "role"),
    ("Engineering Manager, Payments", "role"),
    ("Software Engineering Intern", "internship"),
    ("Technical Program Manager", "role"),
    ("Group Product Manager", "seniority"),
    ("Sr. Product Manager", "seniority"),
])
def test_title_filters(title, expected, cfg):
    assert run(F.PRE_FETCH[:3], job(title), cfg) == expected


def test_role_types():
    assert F.classify_role("AI Engineer") == "SWE"
    assert F.classify_role("Associate Product Manager") == "PM"
    assert F.classify_role("Forward Deployed Engineer") == "SWE"


@pytest.mark.parametrize("loc,bay,remote", [
    ("Palo Alto, CA", True, False),
    ("SF", True, False),
    ("Remote - US", False, True),
    ("Remote in USA", False, True),
    ("Remote - Canada", False, False),
    ("Seattle, WA", False, False),
    ("Dublin, Ireland", False, False),
])
def test_locations(loc, bay, remote):
    assert is_bay_area(loc) == bay
    assert is_remote_us(loc) == remote


def test_location_mode(cfg):
    assert F.location_filter(job(locs=["Seattle, WA"]), cfg) == "location"
    assert F.location_filter(job(locs=["Seattle, WA", "San Francisco, CA"]), cfg) is None
    assert F.location_filter(job(locs=[]), cfg) is None  # unknown → keep


@pytest.mark.parametrize("text,expected", [
    ("5+ years of experience building backend systems", 5),
    ("3+ years of experience, or 1+ with a Master's degree", 1),
    ("0-2 years of professional experience", 0),
    ("BS in CS. Strong fundamentals.", None),
    ("at least 4 years in industry", 4),
])
def test_min_years(text, expected):
    assert F.min_years(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("We are unable to sponsor visas for this role.", "no"),
    ("Candidates must be authorized to work without sponsorship now or in the future.", "no"),
    ("Must be a U.S. citizen.", "no"),
    ("Visa sponsorship is available for this position.", "yes"),
    ("We build payments infrastructure.", "unknown"),
])
def test_sponsorship(text, expected):
    assert F.sponsorship_signal(text) == expected


def test_age(cfg):
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert F.age_filter(job(posted_at=now - timedelta(days=40)), cfg, now) == "age"
    assert F.age_filter(job(posted_at=now - timedelta(days=3)), cfg, now) is None
    assert F.age_filter(job(), cfg, now) is None


def test_dates():
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    assert parse_date(1786469891368).date().isoformat() == "2026-08-11"   # Lever ms
    assert parse_date(1781952599).year == 2026                            # Simplify s
    assert parse_date("Posted Today", now=now).date() == now.date()
    assert parse_date("Posted 30+ Days Ago", now=now).date().isoformat() == "2026-09-03"
    assert parse_date("2026-09-03T13:30:34-04:00").hour == 17
    assert parse_date("2099-01-01T00:00:00Z", now=now) == now             # never in the future
    assert parse_date(None) is None


def test_html_to_text():
    assert html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "Hello & welcome"
    assert "<" not in html_to_text("<ul><li>a</li><li>b</li></ul>")


@pytest.mark.parametrize("url,ats,token,pid", [
    ("https://job-boards.greenhouse.io/stripe/jobs/7012345", "greenhouse", "stripe", "7012345"),
    ("https://boards.greenhouse.io/embed/job_app?for=stripe&token=7012345", "greenhouse", "stripe", "7012345"),
    ("https://jobs.lever.co/palantir/6ed76ce8-4156", "lever", "palantir", "6ed76ce8-4156"),
    ("https://jobs.ashbyhq.com/notion/1fc309c8", "ashby", "notion", "1fc309c8"),
    ("https://jobs.smartrecruiters.com/ServiceNow/744000153333219-sr-software-engineer", "smartrecruiters",
     "ServiceNow", "744000153333219"),
])
def test_parse_ats_url(url, ats, token, pid):
    info = parse_ats_url(url)
    assert (info["ats"], info["token"], info["posting_id"]) == (ats, token, pid)


def test_parse_workday_url():
    info = parse_ats_url("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/"
                         "Research-Scientist_JR2012979")
    assert info["tenant"] == "nvidia" and info["site"] == "NVIDIAExternalCareerSite"
    assert ats_job_id(info) == "workday:nvidia:JR2012979"
    assert parse_ats_url("https://example.com/careers/123") is None


def test_workday_limit_shared_per_cluster():
    from apply_agent.discover.sources.base import Http
    k = Http._limit_key
    assert k("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/x") == k("https://adobe.wd5.myworkdayjobs.com/y") == "wd5.myworkdayjobs.com"
    assert k("https://api.ashbyhq.com/posting-api/job-board/x") == "api.ashbyhq.com"
