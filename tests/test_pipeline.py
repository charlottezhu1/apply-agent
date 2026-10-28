import asyncio
from datetime import datetime, timedelta, timezone

from apply_agent.core.models import Job
from apply_agent.core.state import SeenJobs, fingerprint
from apply_agent.tailor import factcheck
from apply_agent.tailor.inbox import Inbox
from apply_agent.tailor.rank import rank, recency
from apply_agent.prepare.questions import classify
from apply_agent.prepare.sheets import build_rows

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def mk(i, company="Acme", score=80, age=1, title=None):
    return Job(id=f"t:{company}:{i}", source="t", company=company, title=title or f"SWE {i}", url=f"https://x/{i}",
               match_score=score, posted_at=NOW - timedelta(days=age) if age is not None else None, role_type="SWE")


# ---------- state ----------
def test_fingerprint_catches_reposts():
    assert fingerprint("Stripe", "Software Engineer, New Grad (2027)") == \
        fingerprint("Stripe", "Software Engineer - New Grad 2026")
    assert fingerprint("Stripe", "Software Engineer, Payments") != fingerprint("Stripe", "Software Engineer, Infra")


def test_seen(tmp_path):
    s = SeenJobs(tmp_path / "seen.json")
    s.set("a:1", "rejected", fingerprint="acme|swe")
    assert s.is_seen("a:1", "acme|swe")
    assert s.is_seen("a:2", "acme|swe")          # repost with a new id
    s.set("b:1", "failed", fingerprint="b|x")
    assert not s.is_seen("b:1", "b|x")          # failed once → retried
    s.set("b:1", "failed", fingerprint="b|x")
    assert s.is_seen("b:1", "b|x")              # failed twice → left alone
    s.save()
    assert SeenJobs(tmp_path / "seen.json").get("a:1")["status"] == "rejected"


# ---------- ranking ----------
def test_recency():
    j = mk(1, age=0)
    assert recency(j, NOW, 14) == 1.0
    assert abs(recency(mk(1, age=14), NOW, 14) - 0.5) < 1e-9
    assert abs(recency(mk(1, age=28), NOW, 14) - 0.25) < 1e-9
    assert recency(mk(1, age=None), NOW, 14) == 0.5
    assert recency(mk(1, age=-5), NOW, 14) == 1.0  # future → age 0


def test_rank_cap_and_order(cfg):
    jobs = [mk(1, "A", 90, 10), mk(2, "A", 89, 1), mk(3, "A", 88, 1), mk(4, "B", 75, 1), mk(5, "C", 60, 0)]
    out = rank(jobs, None, cfg, NOW, n=4)
    assert sum(j.company == "A" for j in out) == 2            # max 2 per company
    assert "t:A:1" not in [j.id for j in out]                 # older A job loses to the two fresh ones
    assert [j.rank for j in out] == [1, 2, 3, 4]
    assert rank(jobs, None, cfg, NOW, n=4) == out            # deterministic


def test_rank_skips_done(cfg, tmp_path):
    s = SeenJobs(tmp_path / "s.json")
    j = mk(1, "A")
    s.set(j.id, "applied", fingerprint=j.fingerprint)
    assert rank([j], s, cfg, NOW) == []


# ---------- inbox ----------
def test_inbox_roundtrip_and_dedupe(tmp_path):
    p = tmp_path / "questions.md"
    ib = Inbox(p)
    ids = asyncio.run(ib.add({"Stripe_SWE": ["Did you use React at Acme?"],
                              "Figma_SWE": ["Did you use React at Acme?", "Any Kubernetes?"]},
                             "2026-10-03", "unused"))
    assert ids["Stripe_SWE"] == ids["Figma_SWE"][:1]
    assert len(ib.open) == 2
    ib.save()
    md = p.read_text().replace("- Answer: \n", "- Answer: yes, frontend only\nkept on two lines\n", 1)
    p.write_text(md)
    ib2 = Inbox(p)
    q = ib2.answered()[0]
    assert q.answer == "yes, frontend only kept on two lines"
    assert q.asked_by == ["Stripe_SWE", "Figma_SWE"]
    ib2.mark_absorbed({q.id}, "2026-10-04")
    ib2.save()
    ib3 = Inbox(p)
    assert len(ib3.open) == 1 and ib3.done[0].absorbed_on == "2026-10-04"


# ---------- fact check ----------
BASE = r"""\documentclass{article}
\usepackage{x}
\item Raised conversion from 6\% to \textbf{14\%} using Flask.
\item Built a \textbf{RAG} chatbot.
"""


def test_factcheck_clean():
    new = BASE.replace("Built a", "Shipped a")
    assert factcheck.warnings(BASE, new, "", []) == []
    assert factcheck.scaffolding_changed(BASE, new) == []


def test_factcheck_flags():
    new = BASE.replace(r"\textbf{14\%}", r"\textbf{15\%}").replace("chatbot", "chatbot on Kubernetes and AWS")
    w = factcheck.warnings(BASE, new, "", [])
    assert any("number" in x for x in w)
    assert "new term: AWS" in w
    assert factcheck.scaffolding_changed(BASE, BASE.replace(r"\usepackage{x}", r"\usepackage{y}"))


# ---------- preparer ----------
def test_question_classes():
    assert classify("First Name", ["input_text"]) == "profile"
    assert classify("Will you require Stripe to sponsor you?", ["multi_value_single_select"]) == "profile"
    assert classify("What are your salary expectations?", ["input_text"]) == "by_hand"
    assert classify("Do you opt-in to receive WhatsApp messages?", ["multi_value_single_select"]) == "by_hand"
    assert classify("Why do you want to work at Stripe?", ["textarea"]) == "essay"


def test_sheet_rows(cfg):
    header = ["Notes", "Company", "Job Link", "Job Title", "Posted Date"]
    jobs = [mk(1, "A"), mk(2, "B")]
    rows = build_rows(jobs, header, {"https://x/2"}, cfg)
    assert len(rows) == 1
    r = rows[0][1]
    assert r[1] == "A" and r[2] == "https://x/1" and r[0] == "" and r[4] == "2026-10-01"


def test_factcheck_allows_reword_around_number():
    base = r"\item Ran \textbf{30+ user interviews / testing} on \textbf{Supabase} and PostgreSQL."
    new = r"\item Ran \textbf{30+ rounds of user research} on \textbf{Supabase/PostgreSQL}."
    assert factcheck.warnings(base, new, "", []) == []


def test_absorb_active_folders(tmp_path):
    from apply_agent.tailor.absorb import active_folders
    s = SeenJobs(tmp_path / "s.json")
    s.set("a:1", "customized", resume_dir="resume/customized/2026-10-03/Stripe_SWE")
    s.set("a:2", "applied", resume_dir="resume/customized/submitted/Clay_SWE")
    assert active_folders(s) == {"2026-10-03/Stripe_SWE"}


def test_changes_from_diff(tmp_path):
    from apply_agent.tailor.customize import write_changes
    out = tmp_path / "Acme_SWE" / "changes.txt"
    out.parent.mkdir()
    n = write_changes(BASE, BASE.replace("Built a", "Shipped a"), "original_tex/SWE.tex", out)
    text = out.read_text()
    assert n == 1 and "[4] \\item Built a" in text and "-> \\item Shipped a" in text
    assert write_changes(BASE, BASE, "x", out) == 0
