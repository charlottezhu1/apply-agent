"""applicant/ — the profile, its consumers (prompts, config, extension, user-agent), the facts mirror, init and sync."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from apply_agent import init as init_mod
from apply_agent import sync as sync_mod
from apply_agent.core import config as C
from apply_agent.core import profile as P
from apply_agent.core import resume_text as RT
from apply_agent.core.models import Job
from apply_agent.discover import filters as F
from apply_agent.discover.sources import base
from apply_agent.prepare import answers
from apply_agent.score import run as score
from apply_agent.tailor import absorb

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "applicant"


@pytest.fixture
def prof():
    return P.load_profile()


def write_profile(tmp_path, data: dict) -> Path:
    p = tmp_path / "profile.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


MINIMAL = {"name": {"first": "A", "last": "B"}, "contact": {"email": "a@b.co"}}


# ---------- the profile itself ----------
def test_tests_use_the_fixture_applicant_not_the_real_one():
    assert C.APPLICANT != ROOT / "applicant"
    assert P.load_profile().contact.email == "sam.tester@example.com"


def test_derived_names_and_pronouns(prof):
    assert prof.first_name == "Sam" and prof.full_name == "Sam Tester"
    assert prof.pronoun == ("they", "them", "their")


@pytest.mark.parametrize("pron,forms", [("she/her", ("she", "her", "her")), ("he/him", ("he", "him", "his")),
                                        ("they/them", ("they", "them", "their"))])
def test_pronoun_forms(tmp_path, pron, forms):
    d = {**MINIMAL, "name": {**MINIMAL["name"], "pronouns": pron}}
    assert P.load_profile(write_profile(tmp_path, d)).pronoun == forms


def test_bad_pronouns_typos_and_numbers(tmp_path):
    with pytest.raises(P.ProfileError, match="pronouns"):
        P.load_profile(write_profile(tmp_path, {**MINIMAL, "name": {**MINIMAL["name"], "pronouns": "xe/xem"}}))
    with pytest.raises(P.ProfileError, match="educaton"):   # typo'd section is an error, not ignored
        P.load_profile(write_profile(tmp_path, {**MINIMAL, "educaton": {}}))
    p = P.load_profile(write_profile(tmp_path, {**MINIMAL, "address": {"zip": 94305}, "education": {"gpa": 3.9}}))
    assert p.address.zip == "94305" and p.education.gpa == "3.9"   # YAML numbers become text


def test_missing_profile_says_how_to_fix(tmp_path):
    with pytest.raises(P.ProfileError, match="python -m apply_agent.init"):
        P.load_profile(tmp_path / "nope.yaml")


def test_template_must_be_filled_in(tmp_path):
    with pytest.raises(P.ProfileError, match="CHANGE_ME"):
        P.load_profile(TEMPLATE / "profile.yaml")
    filled = tmp_path / "profile.yaml"
    filled.write_text((TEMPLATE / "profile.yaml").read_text().replace("CHANGE_ME", "x"))
    assert P.load_profile(filled).contact.email == "x"   # otherwise valid as shipped


def test_yaml_nulls_mean_default(tmp_path):
    p = P.load_profile(write_profile(tmp_path, {**MINIMAL, "settings": None, "eeo": None,
                                                "contact": {"email": "a@b.co", "phone": None}}))
    assert p.settings == {} and p.eeo.gender == "" and p.contact.phone == ""


# ---------- extension values ----------
def test_form_profile_for_fixture(prof):
    assert prof.form_profile() == {
        "firstName": "Sam", "lastName": "Tester", "legalFirstName": "Samuel", "preferredFirstName": "Sam",
        "fullName": "Sam Tester", "fullLegalName": "Samuel Tester", "fullPreferredName": "Sam Tester",
        "email": "sam.tester@example.com", "phone": "5550100", "github": "https://github.com/sam-tester",
        "address": "1 Example Way", "city": "Exampleton", "state": "California", "stateAbbr": "CA",
        "zip": "94000", "country": "United States", "school": "Example University",
        "degree": "Bachelor of Science", "degreeOptions": ["Bachelor's", "BS"], "major": "Computer Science",
        "eduEndYear": "2028", "gradYear": "2028", "gpa": "3.5", "currentTitle": "Student", "answerNo": "No",
        "workAuthorized": "Yes", "needsSponsorship": "Yes", "gender": "nonbinary"}


def test_form_profile_omits_empty_and_legal_name_falls_back(tmp_path):
    p = P.load_profile(write_profile(tmp_path, MINIMAL))
    f = p.form_profile()
    assert "legalFirstName" not in f and "fullLegalName" not in f and "phone" not in f
    assert f["preferredFirstName"] == "A" and f["fullPreferredName"] == "A B"


def test_every_key_the_extension_rules_use_can_be_supplied(tmp_path):
    """Drift guard: a RULES key in config.js with no source in the profile would silently never fill."""
    full = {**MINIMAL, "name": {**MINIMAL["name"], "middle": "M"}, "contact": {**MINIMAL["contact"], "phone": "1",
            "linkedin": "l", "github": "g", "website": "w"},
            "address": {k: "x" for k in ("street", "city", "state", "state_abbr", "zip", "country")},
            "education": {**{k: "x" for k in ("school", "undergrad_school", "degree", "major", "start_month",
                                              "start_year", "end_month", "end_year", "grad_year", "grad_date",
                                              "gpa", "test_scores")}, "degree_options": ["x"]},
            "current": {"company": "x", "title": "x"}, "eeo": {k: "x" for k in ("veteran", "disability",
                                                                                "gender", "race")}}
    supplied = set(P.load_profile(write_profile(tmp_path, full)).form_profile())
    derived = {"SKIP", "location", "nameFull", "nameLastFirst", "nameFirst", "nameLast", "namePreferred",
               "nameMiddle"}   # computed in content.js from the name fields
    used = set(re.findall(r'key: "(\w+)"', (ROOT / "chrome_extension" / "config.js").read_text()))
    assert used - derived - supplied == set()


def test_write_extension_profile(tmp_path, prof):
    dest = P.write_extension_profile(prof, tmp_path / "gen" / "profile.json")
    assert json.loads(dest.read_text()) == prof.form_profile()
    assert not list(dest.parent.glob("*.tmp"))


def test_content_script_loads_profile_json():
    js = (ROOT / "chrome_extension" / "content.js").read_text()
    assert "generated/profile.json" in js and "await loadProfile()" in js
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text())
    assert "generated/profile.json" in manifest["web_accessible_resources"][0]["resources"]
    assert "const PROFILE" not in (ROOT / "chrome_extension" / "config.js").read_text()


# ---------- config = defaults + the profile's settings ----------
def test_deep_merge():
    base = {"a": 1, "s": {"x": 1, "y": [1, 2], "z": {"k": 1}}}
    over = {"s": {"y": [9], "z": {"j": 2}}, "b": 2}
    assert C.deep_merge(base, over) == {"a": 1, "b": 2, "s": {"x": 1, "y": [9], "z": {"k": 1, "j": 2}}}
    assert base["s"]["y"] == [1, 2]   # inputs are not mutated


def test_load_config_merges_profile_settings_over_defaults():
    cfg = C.load_config()
    assert cfg.jobs_per_day == 4                                  # profile wins
    assert cfg.search.location_mode == "us" and cfg.search.degree_path is False
    assert cfg.search.max_years_required == 2 and cfg.search.roles == ["SWE", "PM"]   # other keys keep defaults
    assert cfg.candidate.base_resumes.SWE == "resume/original_tex/SWE_Sam_Resume.tex"
    assert cfg.matchmaker.model and cfg.sheet.enabled is False    # generic defaults untouched


def test_config_yaml_is_generic():
    text = (ROOT / "config.yaml").read_text()
    assert not re.search(r'id: "[A-Za-z0-9_-]{20,}"', text)   # no sheet id
    cfg = C.load_config(ROOT / "config.yaml")                     # explicit path: no profile needed
    assert cfg.sheet.enabled is False and cfg.search.degree_path is True


# ---------- prompts use the profile ----------
def _job():
    return Job(id="t:x:1", source="t", company="Acme", title="Software Engineer", url="https://x",
               description="d", locations=["Remote"], role_type="SWE")


def test_scorer_prompt_uses_profile(monkeypatch):
    monkeypatch.setattr(score, "base_resume_text", lambda p: "R")
    monkeypatch.setattr(score, "confirmed_facts", lambda: "F")   # the real ones come from the private resume repo
    monkeypatch.setattr(score, "preferences", lambda: "P")
    cfg = SimpleNamespace(candidate=SimpleNamespace(base_resumes=SimpleNamespace(SWE="a", PM="b")),
                          search=SimpleNamespace(location_mode="us"))
    system, _ = score.build_prompts(_job(), cfg)
    assert "the candidate has a BS in CS from Example University (graduating May 2028) and wants roles" in system
    assert "things they must never claim" in system
    assert "Stanford" not in system and " she " not in system


def test_answer_and_absorb_prompts_use_profile():
    a = answers.system_prompt()
    assert "Sam Tester (Example University BS CS, graduating May 2028)" in a and "Sam's voice" in a
    b = " ".join(absorb.system_prompt().split())
    assert "Sam's answer" in b and "their resume-tailoring rules" in b and "they did it" in b
    assert not re.search(r"\b(she|her)\b", b)


def test_education_fallbacks(tmp_path):
    p = P.load_profile(write_profile(tmp_path, {**MINIMAL, "education": {"school": "S", "degree": "BS"}}))
    assert p.education_blurb == "BS from S" and p.education_tagline == "S BS"
    bare = P.load_profile(write_profile(tmp_path / "x", MINIMAL)) if (tmp_path / "x").mkdir() is None else None
    assert bare.education_blurb and bare.education_tagline


# ---------- user agent ----------
def test_user_agent_names_contact(monkeypatch):
    assert base.user_agent() == "apply_agent/1.0 (personal job search; contact: sam.tester@example.com)"
    assert base.Http().client.headers["User-Agent"] == base.user_agent()
    monkeypatch.setattr(P, "load_profile", lambda *a: (_ for _ in ()).throw(P.ProfileError("x")))
    assert base.user_agent() == "apply_agent/1.0 (personal job search)"


# ---------- years rule: degree path is a setting ----------
JD = "3+ years of experience, or 1+ with a Master's degree"


def test_degree_path_setting():
    assert F.min_years(JD) == 1 and F.min_years(JD, degree_path=False) == 3
    job = Job(id="t:x:1", source="t", company="Acme", title="SWE", url="u", description=JD)
    on = SimpleNamespace(search=SimpleNamespace(max_years_required=2, degree_path=True))
    off = SimpleNamespace(search=SimpleNamespace(max_years_required=2, degree_path=False))
    assert F.years_filter(job, on) is None
    assert F.years_filter(job, off) == "years"
    assert F.years_filter(job, SimpleNamespace(search=SimpleNamespace(max_years_required=2))) is None  # default: on


# ---------- facts mirror ----------
@pytest.fixture
def mirror(tmp_path, monkeypatch):
    facts, resume = tmp_path / "applicant" / "facts.md", tmp_path / "resume"
    facts.parent.mkdir(), resume.mkdir()
    facts.write_text("# Facts\n\n## Confirmed\n- one\n")
    for mod in (RT, absorb):
        monkeypatch.setattr(mod, "CONFIRMED_FACTS", facts)
    monkeypatch.setattr(RT, "RESUME", resume)
    monkeypatch.setattr(RT, "FACTS_MIRROR", resume / "confirmed_facts.md")
    RT.confirmed_facts.cache_clear()
    yield facts, resume / "confirmed_facts.md"
    RT.confirmed_facts.cache_clear()


def test_facts_mirror_created_and_idempotent(mirror):
    facts, m = mirror
    assert RT.sync_facts_mirror() is True
    assert m.read_text() == RT.MIRROR_HEADER + facts.read_text()
    assert RT.sync_facts_mirror() is False
    facts.write_text(facts.read_text() + "- two\n")
    assert RT.sync_facts_mirror() is True and "- two" in m.read_text()


def test_mirror_skipped_without_source_or_resume_repo(mirror):
    facts, m = mirror
    facts.unlink()
    assert RT.sync_facts_mirror() is False and not m.exists()


def test_absorbing_answers_refreshes_the_mirror(mirror):
    facts, m = mirror
    absorb._append([("confirmed", "two (Q-2)")])
    assert "- two (Q-2)" in facts.read_text() and "- two (Q-2)" in m.read_text()


def test_sync_command(mirror, tmp_path, monkeypatch):
    out = tmp_path / "gen" / "profile.json"
    monkeypatch.setattr(sync_mod, "EXT_PROFILE", out)
    monkeypatch.setattr(sync_mod, "write_extension_profile", lambda prof: P.write_extension_profile(prof, out))
    assert sync_mod.sync() == [str(out), str(RT.FACTS_MIRROR)]
    assert json.loads(out.read_text())["email"] == "sam.tester@example.com"
    assert sync_mod.sync() == [str(out)]   # facts copy already current


# ---------- init ----------
def test_init_creates_templates_and_never_overwrites(tmp_path, monkeypatch):
    dest = tmp_path / "applicant"
    monkeypatch.setattr(init_mod, "APPLICANT", dest)
    created = init_mod.init()
    assert set(created) == {"profile.yaml", "preferences.md", "facts.md", "README.md"}
    (dest / "preferences.md").write_text("MINE")
    assert init_mod.init() == [] and (dest / "preferences.md").read_text() == "MINE"
    assert (dest / "facts.md").read_text().startswith("# Confirmed facts")


def test_template_facts_match_what_absorb_expects():
    """absorb appends under these exact headings; the shipped template must have them."""
    lines = (TEMPLATE / "facts.md").read_text().splitlines()
    assert "## Confirmed" in lines and "## Never claim" in lines


def test_resume_tag_is_the_first_name_made_filename_safe():
    p = P.load_profile()                                  # the made-up fixture applicant
    assert P.resume_tag(p) == "Sam"
    named = lambda first: p.model_copy(update={"name": p.name.model_copy(update={"first": first})})
    assert P.resume_tag(named("Mary-Ann O'Neil")) == "MaryAnnONeil"
    assert P.resume_tag(named("!!!")) == "Applicant"      # nothing usable: never an empty file name
