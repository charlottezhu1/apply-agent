"""applicant/profile.yaml: who the applicant is. Hand-edited, read-only for the pipeline.

One file feeds three things:
  * the LLM prompts (name, pronouns, education blurb),
  * the Chrome extension's autofill values (written to chrome_extension/generated/profile.json),
  * `settings:`, the applicant's own overrides of config.yaml (see config.load_config).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from .config import APPLICANT, EXT_GENERATED

PROFILE_PATH = APPLICANT / "profile.yaml"
EXT_PROFILE = EXT_GENERATED / "profile.json"

PRONOUNS = {  # subject, object, possessive
    "she/her": ("she", "her", "her"),
    "he/him": ("he", "him", "his"),
    "they/them": ("they", "them", "their"),
}


class ProfileError(RuntimeError):
    """The profile is missing or invalid. The message says how to fix it."""


class _M(BaseModel):
    # forbid: a typo in profile.yaml is an error, not a silently ignored field.
    # coerce_numbers_to_str: YAML reads `zip: 94305` and `gpa: 3.9` as numbers; they are form text here.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _null_means_default(cls, data):
        # `phone:` or a section whose lines are all commented out parses as null in YAML: treat it as not set.
        return {k: v for k, v in data.items() if v is not None} if isinstance(data, dict) else data


class Name(_M):
    first: str                      # everyday first name
    last: str
    middle: str = ""
    legal_first: str = ""           # used for fields labelled legal / passport / government
    legal_middle: str = ""
    legal_last: str = ""            # defaults to `last`
    preferred_first: str = ""       # defaults to `first`
    pronouns: str = "they/them"     # she/her | he/him | they/them

    @field_validator("pronouns")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in PRONOUNS:
            raise ValueError(f"pronouns must be one of {sorted(PRONOUNS)} (got {v!r})")
        return v


class Contact(_M):
    email: str
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


class Address(_M):
    street: str = ""
    city: str = ""
    state: str = ""
    state_abbr: str = ""
    zip: str = ""
    country: str = ""


class Education(_M):
    school: str = ""
    undergrad_school: str = ""
    degree: str = ""
    degree_options: list[str] = []  # tried in order for degree dropdowns ("Master's" vs "Master of Science")
    major: str = ""
    start_month: str = ""
    start_year: str = ""
    end_month: str = ""
    end_year: str = ""
    grad_year: str = ""
    grad_date: str = ""             # MM/DD/YYYY
    gpa: str = ""
    test_scores: str = ""
    blurb: str = ""                 # in prompts: "the candidate has <blurb>", e.g. "an MS in CS from X (graduating June 2027)"
    tagline: str = ""               # in prompts: "<name> (<tagline>)", e.g. "X MS CS, graduating June 2027"


class Current(_M):
    company: str = ""
    title: str = ""


class WorkAuthorization(_M):
    authorized: str = "Yes"         # "Are you legally authorized to work in the US?"
    needs_sponsorship: str = "No"   # "Will you now or in the future require sponsorship?"


class Eeo(_M):                      # matched against dropdown option text; leave empty to never fill
    veteran: str = ""
    disability: str = ""
    gender: str = ""
    race: str = ""


class Form(_M):
    answer_no: str = "No"


def _flat(d: dict, prefix: str = ""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flat(v, f"{prefix}{k}.")
        else:
            yield f"{prefix}{k}", v


class Profile(_M):
    name: Name
    contact: Contact
    address: Address = Address()
    education: Education = Education()
    current: Current = Current()
    work_authorization: WorkAuthorization = WorkAuthorization()
    eeo: Eeo = Eeo()
    form: Form = Form()
    settings: dict = {}             # overrides for config.yaml, same keys (jobs_per_day, search, sheet, candidate, ...)

    @model_validator(mode="after")
    def _no_placeholders(self):
        left = [k for k, v in _flat(self.model_dump()) if v == "CHANGE_ME"]
        if left:
            raise ValueError(f"replace CHANGE_ME in: {', '.join(left)}")
        return self

    # ---- derived ----
    @property
    def first_name(self) -> str:
        return self.name.first

    @property
    def full_name(self) -> str:
        return f"{self.name.first} {self.name.last}"

    @property
    def pronoun(self) -> tuple[str, str, str]:
        return PRONOUNS[self.name.pronouns]

    @property
    def education_blurb(self) -> str:
        e = self.education
        return e.blurb or " from ".join(x for x in (e.degree, e.school) if x) or "an unstated education"

    @property
    def education_tagline(self) -> str:
        e = self.education
        return e.tagline or " ".join(x for x in (e.school, e.degree) if x) or "education not stated"

    # ---- extension ----
    def form_profile(self) -> dict:
        """The flat key set chrome_extension/content.js reads (PROFILE). Empty values are left out."""
        n, c, a, e = self.name, self.contact, self.address, self.education
        pref = n.preferred_first or n.first
        legal_first, legal_last = n.legal_first or n.first, n.legal_last or n.last
        out = {
            "firstName": n.first, "lastName": n.last, "middleName": n.middle,
            "legalFirstName": n.legal_first, "legalMiddleName": n.legal_middle, "legalLastName": n.legal_last,
            "preferredFirstName": pref, "fullName": self.full_name,
            "fullLegalName": f"{legal_first} {legal_last}" if (n.legal_first or n.legal_last) else "",
            "fullPreferredName": f"{pref} {n.last}",
            "email": c.email, "phone": c.phone, "linkedin": c.linkedin, "github": c.github, "website": c.website,
            "address": a.street, "city": a.city, "state": a.state, "stateAbbr": a.state_abbr, "zip": a.zip,
            "country": a.country,
            "school": e.school, "undergradSchool": e.undergrad_school, "degree": e.degree,
            "degreeOptions": e.degree_options, "major": e.major,
            "eduStartMonth": e.start_month, "eduStartYear": e.start_year, "eduEndMonth": e.end_month,
            "eduEndYear": e.end_year, "gradYear": e.grad_year, "gradDate": e.grad_date, "gpa": e.gpa,
            "testScores": e.test_scores,
            "currentCompany": self.current.company, "currentTitle": self.current.title,
            "answerNo": self.form.answer_no, "workAuthorized": self.work_authorization.authorized,
            "needsSponsorship": self.work_authorization.needs_sponsorship,
            "veteran": self.eeo.veteran, "disability": self.eeo.disability, "gender": self.eeo.gender,
            "race": self.eeo.race,
        }
        return {k: v for k, v in out.items() if v not in ("", [], None)}

    # ---- prompts ----
    def prompt_vars(self) -> dict:
        subj, obj, poss = self.pronoun
        return {"name": self.first_name, "full_name": self.full_name, "subj": subj, "obj": obj, "poss": poss,
                "education_blurb": self.education_blurb, "education_tagline": self.education_tagline}


@lru_cache
def load_profile(path: Path | None = None) -> Profile:
    p = Path(path) if path else PROFILE_PATH
    if not p.exists():
        raise ProfileError(f"{p} not found. Create it from the template with: python -m apply_agent.init")
    try:
        return Profile.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    except (ValidationError, yaml.YAMLError) as e:
        if isinstance(e, ValidationError):  # one line per problem, without pydantic's echo of the input values
            e = "\n".join(f"  {'.'.join(map(str, x['loc'])) or 'profile'}: {x['msg'].removeprefix('Value error, ')}"
                          for x in e.errors())
        raise ProfileError(f"{p} is invalid:\n{e}") from None


def resume_tag(profile: Profile | None = None) -> str:
    """The applicant's first name as used in tailored resume file names: <Company>_<ROLE>_<tag>_Resume.tex/.pdf.
    Letters and digits only, so it is always safe in a file name."""
    return re.sub(r"[^A-Za-z0-9]+", "", (profile or load_profile()).first_name) or "Applicant"


def write_extension_profile(profile: Profile | None = None, dest: Path | None = None) -> Path:
    """chrome_extension/generated/profile.json, read by the extension on every fill."""
    dest = dest or EXT_PROFILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps((profile or load_profile()).form_profile(), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(dest)
    return dest
