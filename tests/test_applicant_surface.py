"""Characterization tests for everything that reads the applicant's own files (facts, preferences, prompts,
config, packets, step 0). They pin today's behaviour so the applicant/ regroup can be checked against it."""
import json
from types import SimpleNamespace

import pytest

from apply_agent.core import config as C
from apply_agent.core import resume_text as RT
from apply_agent.core import step0
from apply_agent.core.models import Job
from apply_agent.core.profile import resume_tag
from apply_agent.core.state import SeenJobs
from apply_agent.prepare import run as prep
from apply_agent.score import run as score
from apply_agent.tailor import absorb


@pytest.fixture(autouse=True)
def _clear_caches():
    RT.confirmed_facts.cache_clear()
    RT.preferences.cache_clear()
    yield
    RT.confirmed_facts.cache_clear()
    RT.preferences.cache_clear()


# ---------- facts + preferences ----------
CUSTOMIZE = "# x\n\n## Parse\nignore me\n\n## Pre-approved edits — apply these\nPRE-1\nPRE-2\n\n## Steps\nnot included\n"


def test_confirmed_facts_combines_pre_approved_and_learned(tmp_path, monkeypatch):
    cm, facts = tmp_path / "customize.md", tmp_path / "facts.md"
    cm.write_text(CUSTOMIZE)
    facts.write_text("# Confirmed facts\n\n## Confirmed\n- LEARNED\n")
    monkeypatch.setattr(RT, "CUSTOMIZE_MD", cm)
    monkeypatch.setattr(RT, "CONFIRMED_FACTS", facts)
    out = RT.confirmed_facts()
    assert "PRE-1" in out and "PRE-2" in out and "LEARNED" in out
    assert "ignore me" not in out and "not included" not in out
    assert out.index("PRE-1") < out.index("LEARNED")


def test_confirmed_facts_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(RT, "CUSTOMIZE_MD", tmp_path / "nope.md")
    monkeypatch.setattr(RT, "CONFIRMED_FACTS", tmp_path / "nope2.md")
    assert RT.confirmed_facts() == ""


def test_preferences_read_and_missing(tmp_path, monkeypatch):
    p = tmp_path / "preferences.md"
    monkeypatch.setattr(RT, "PREFERENCES", p)
    assert RT.preferences() == ""
    RT.preferences.cache_clear()
    p.write_text("\n# Prefs\n- no kubernetes\n\n")
    assert RT.preferences() == "# Prefs\n- no kubernetes"


def test_base_resume_text_needs_compiled_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(RT, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="compile the base resume"):
        RT.base_resume_text("original_tex/X.tex")


# ---------- absorbing answers into facts ----------
def test_append_creates_file_and_orders_sections(tmp_path, monkeypatch):
    f = tmp_path / "facts.md"
    monkeypatch.setattr(absorb, "CONFIRMED_FACTS", f)
    absorb._append([("confirmed", "A (Q-1)"), ("never_claim", "B (Q-2)"), ("confirmed", "C (Q-3)")])
    lines = f.read_text().splitlines()
    assert lines.index("## Confirmed") < lines.index("## Never claim")
    conf = lines[lines.index("## Confirmed"):lines.index("## Never claim")]
    assert [l for l in conf if l.startswith("- ")] == ["- A (Q-1)", "- C (Q-3)"]
    assert "- B (Q-2)" in lines


def test_append_keeps_existing_and_inserts_above_trailing_blank(tmp_path, monkeypatch):
    f = tmp_path / "facts.md"
    f.write_text("# Confirmed facts\n\n## Confirmed\n- old\n\n\n## Never claim\n- no-go\n")
    monkeypatch.setattr(absorb, "CONFIRMED_FACTS", f)
    absorb._append([("confirmed", "new")])
    assert f.read_text() == "# Confirmed facts\n\n## Confirmed\n- old\n- new\n\n\n## Never claim\n- no-go\n"


# ---------- scorer prompt ----------
def _scfg():
    return SimpleNamespace(
        candidate=SimpleNamespace(base_resumes=SimpleNamespace(SWE="a.tex", PM="b.tex")),
        search=SimpleNamespace(location_mode="bay_area_or_remote"))


def _job(desc="Build {things} with {braces}.", role="SWE"):
    return Job(id="t:x:1", source="t", company="Acme", title="Software Engineer", url="https://x",
               description=desc, locations=["San Francisco, CA"], role_type=role)


def test_build_prompts_fills_everything(monkeypatch):
    monkeypatch.setattr(score, "base_resume_text", lambda p: f"RESUME[{p}]")
    monkeypatch.setattr(score, "confirmed_facts", lambda: "FACTS-X")
    monkeypatch.setattr(score, "preferences", lambda: "PREFS-X")
    system, user = score.build_prompts(_job(), _scfg())
    assert "RESUME[a.tex]" in system and "FACTS-X" in system and "PREFS-X" in system
    assert "Bay Area, or remote within the US" in system
    assert "{" not in system.replace("{things}", "")  # no unfilled placeholder in the system prompt
    assert "Company: Acme" in user and "Build {things} with {braces}." in user  # JD braces survive untouched
    assert score.build_prompts(_job(role="PM"), _scfg())[0].count("RESUME[b.tex]") == 1


def test_build_prompts_empty_jd(monkeypatch):
    monkeypatch.setattr(score, "base_resume_text", lambda p: "R")
    _, user = score.build_prompts(_job(desc=""), _scfg())
    assert "No job description available" in user


def test_apply_verdict_caps_and_needs():
    v = score.Verdict(role_ok=True, level_ok=True, location_ok=True, sponsorship_ok=True, preferences_ok=False,
                      needs_reasons="x", match_score=250, alignment=["a"] * 9, gaps=["g"])
    j = score.apply_verdict(_job(), v)
    assert j.match_score == 100 and len(j.alignment) == 5 and j.meets_needs is False
    v.preferences_ok = True
    assert score.apply_verdict(_job(desc=""), v).match_score == score.EMPTY_JD_CAP


# ---------- config ----------
def test_load_config_namespace(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("a: 1\nb:\n  c: [1, 2]\n  d: {e: x}\n")
    cfg = C.load_config(p)
    assert cfg.a == 1 and cfg.b.c == [1, 2] and cfg.b.d.e == "x"


def test_run_dir_and_error_log(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "RUNS", tmp_path / "runs")
    cfg = SimpleNamespace(timezone="America/Los_Angeles")
    rd = C.run_dir(cfg, "2026-10-03")
    assert rd == tmp_path / "runs" / "2026-10-03" and rd.is_dir()
    C.log_error(rd, "stage", "job:1", "boom")
    row = json.loads((rd / "errors.jsonl").read_text())
    assert row["stage"] == "stage" and row["job_id"] == "job:1" and row["error"] == "boom"


# ---------- extension packets ----------
def test_match_keys():
    j = _job()
    j.id, j.url = "greenhouse:acme:12345678", "https://boards.greenhouse.io/acme/jobs/12345678"
    j.extra = {"gh_id": 12345678}
    assert prep.match_keys(j) == sorted({"12345678", j.url})


def test_build_packets(tmp_path, monkeypatch):
    folder = tmp_path / "resume" / "customized" / "2026-10-03" / "Acme_SWE"
    folder.mkdir(parents=True)
    j = _job()
    j.id, j.extra = "greenhouse:acme:12345678", {"gh_id": 12345678}
    (folder / "job.json").write_text(j.model_dump_json())
    (folder / "answers.json").write_text(json.dumps({"Why Acme?": "Because."}))
    (folder / f"Acme_SWE_{resume_tag()}_Resume.pdf").write_bytes(b"%PDF")
    seen = SeenJobs(tmp_path / "seen.json")
    seen.set(j.id, "customized", resume_dir="resume/customized/2026-10-03/Acme_SWE")
    seen.set("legacy:x", "customized", resume_dir="")  # no folder → skipped
    monkeypatch.setattr(prep, "ROOT", tmp_path)
    monkeypatch.setattr(prep, "PACKETS", tmp_path / "gen" / "packets.json")
    monkeypatch.setattr(prep, "SeenJobs", lambda: seen)
    assert prep.build_packets() == 1
    (p,) = json.loads((tmp_path / "gen" / "packets.json").read_text())
    assert p["company"] == "Acme" and p["answers"] == {"Why Acme?": "Because."} and "12345678" in p["match"]
    assert p["resume_pdf"].endswith(f"Acme_SWE_{resume_tag()}_Resume.pdf")


# ---------- step 0 ----------
def test_mark_applied_and_expire(tmp_path, monkeypatch):
    sub = tmp_path / "resume" / "customized" / "submitted"
    (sub / "Acme_SWE").mkdir(parents=True)
    (sub / "Acme_SWE" / "job.json").write_text(json.dumps({"id": "g:acme:1"}))
    monkeypatch.setattr(step0, "ROOT", tmp_path)
    monkeypatch.setattr(step0, "SUBMITTED", sub)
    s = SeenJobs(tmp_path / "seen.json")
    s.set("g:acme:1", "customized", resume_dir="resume/customized/2026-10-03/Acme_SWE")
    s.set("g:old:2", "customized", resume_dir="resume/customized/2026-09-01/Old_SWE")
    s.jobs["g:old:2"]["updated"] = "2020-01-01"
    assert step0.mark_applied(s) == ["g:acme:1"]
    assert s.get("g:acme:1")["status"] == "applied"
    assert s.get("g:acme:1")["resume_dir"] == "resume/customized/submitted/Acme_SWE"
    assert step0.expire(s, 14) == ["g:old:2"] and s.get("g:old:2")["status"] == "expired"
