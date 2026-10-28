"""Loads config.yaml and .env. Every stage reads settings from here, never hard-coded."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# Everything about the applicant that is stable (profile, preferences, learned facts). Gitignored.
# APPLY_AGENT_APPLICANT overrides the location (tests use a throwaway folder).
APPLICANT = Path(os.environ.get("APPLY_AGENT_APPLICANT") or ROOT / "applicant")
DATA = ROOT / "data"
RUNS = ROOT / "runs"
EXT_GENERATED = ROOT / "chrome_extension" / "generated"  # files the pipeline writes for the extension


def _ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


def deep_merge(base: dict, over: dict) -> dict:
    """`over` wins; nested dicts merge key by key; everything else (lists, scalars) is replaced."""
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | None = None) -> SimpleNamespace:
    """config.yaml (generic defaults) + the `settings:` block of applicant/profile.yaml (the applicant's own
    choices). An explicit `path` loads just that file, with no profile."""
    load_dotenv(ROOT / ".env")
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if path is None:
        from .profile import load_profile  # imported here: profile.py imports this module
        raw = deep_merge(raw, load_profile().settings)
    return _ns(raw)


def tz(cfg) -> ZoneInfo:
    return ZoneInfo(getattr(cfg, "timezone", "America/Los_Angeles"))


def today_str(cfg) -> str:
    return datetime.now(tz(cfg)).strftime("%Y-%m-%d")


def run_dir(cfg, date: str | None = None) -> Path:
    d = RUNS / (date or today_str(cfg))
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_error(run_dir: Path, stage: str, job_id: str | None, error: str) -> None:
    with open(run_dir / "errors.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"stage": stage, "job_id": job_id, "error": error[:2000],
                            "at": datetime.now().isoformat(timespec="seconds")}) + "\n")
