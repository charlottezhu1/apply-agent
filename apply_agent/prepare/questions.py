"""Find each application's questions and sort them into profile / by-hand / essay."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from apply_agent.core.models import Job
from apply_agent.discover.sources.base import Http

PROFILE = re.compile(
    r"\b(first|last|full|legal|preferred|middle)\s*name\b|^name\b|e-?mail|phone|linkedin|github|website|portfolio|"
    r"\burl\b|resume|\bcv\b|address|city|state|zip|postal|country|location|where.*(live|reside|located)|school|"
    r"university|degree|discipline|major|gpa|graduat|authori[sz]ed|sponsor|visa|veteran|disabilit|gender|race|"
    r"ethnic|hispanic|pronoun|sexual orientation|transgender|current (company|employer|title)|employer|"
    r"job title", re.I)
BY_HAND = re.compile(
    r"salary|compensation|pay expectation|start date|available to start|notice period|previously (worked|employed)|"
    r"ever been employed|worked (here|at|for) .* before|referr|who referred|how did you (hear|find)|relocat|"
    r"in[- ]office|office .* days|days per week|hybrid|on-?site|conflict of interest|non-?compete|criminal|"
    r"background check|signature|certif(y|ication) that|age\b|18 years|family members?|relatives?|"
    r"whatsapp|text messages|\bsms\b|privacy|consent|acknowledge|data processing", re.I)
ESSAY = re.compile(r"why|tell us|describe|what (excites|interests|draws|motivates)|interest(ed)? in|"
                   r"project|proud|anything else|additional information|cover letter|share|walk us through", re.I)


def classify(label: str, field_types: list[str]) -> str:
    l = label.strip()
    if PROFILE.search(l) and not ESSAY.search(l):
        return "profile"
    if BY_HAND.search(l):
        return "by_hand"
    if "textarea" in field_types or ESSAY.search(l):
        return "essay"
    if any(t.startswith("multi_value") for t in field_types):
        return "by_hand"
    return "by_hand"


async def fetch_questions(http: Http, job: Job) -> list[tuple[str, list[str]]] | None:
    """[(label, field_types)] or None when the source has no questions API."""
    if job.source == "greenhouse" or (job.source == "simplify" and (job.extra.get("ats") or {}).get("ats") == "greenhouse"):
        token = job.extra.get("token") or (job.extra.get("ats") or {}).get("token")
        pid = job.extra.get("gh_id") or (job.extra.get("ats") or {}).get("posting_id")
        if not (token and pid):
            return None
        d = await http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{pid}",
                                params={"questions": "true"})
        return [(q.get("label", ""), [f.get("type", "") for f in q.get("fields", [])]) for q in d.get("questions", [])]
    if job.source == "lever":
        url = (job.extra.get("apply_url") or job.url.rstrip("/") + "/apply")
        r = await http.request("GET", url)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for card in soup.select(".application-question, li.application-question"):
            label = card.select_one(".application-label, .text")
            if not label:
                continue
            types = ["textarea"] if card.find("textarea") else ["input_text"]
            out.append((label.get_text(" ", strip=True).rstrip("✱*").strip(), types))
        return out
    return None


async def essay_questions(http: Http, job: Job, cover_letter: bool) -> tuple[list[str], list[str]]:
    """(essay questions to draft, questions to fill by hand)."""
    try:
        qs = await fetch_questions(http, job)
    except Exception:
        qs = None
    essays, by_hand = [], []
    if qs:
        for label, types in qs:
            kind = classify(label, types)
            if kind == "essay":
                if re.search(r"cover letter", label, re.I) and not cover_letter:
                    continue
                essays.append(label)
            elif kind == "by_hand":
                by_hand.append(label)
    if cover_letter and not any("cover letter" in e.lower() for e in essays):
        essays.append("Cover letter")
    return essays[:6], by_hand
