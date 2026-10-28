"""The Job record passed between all stages. Stages only ADD fields."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Job(BaseModel):
    # ---- listener ----
    id: str
    source: str
    company: str
    title: str
    role_type: Literal["SWE", "PM"] | None = None
    locations: list[str] = Field(default_factory=list)
    bay_area: bool = False
    remote_us: bool = False
    url: str
    posted_at: datetime | None = None
    description: str = ""
    sponsorship_signal: Literal["yes", "no", "unknown"] = "unknown"
    years_required_min: int | None = None
    fingerprint: str = ""
    extra: dict = Field(default_factory=dict)  # source-specific bits (board token, posting id, ...)

    # ---- matchmaker ----
    match_score: int | None = None
    alignment: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    meets_needs: bool | None = None
    needs_reasons: str = ""

    # ---- apply_agent.tailor ----
    rank_score: float | None = None
    rank: int | None = None
    resume_dir: str | None = None
    resume_pdf: str | None = None
    customize_status: Literal["ok", "needs_answers", "failed"] | None = None
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # ---- apply_agent.prepare ----
    answers: dict[str, str] = Field(default_factory=dict)
    fill_by_hand: list[str] = Field(default_factory=list)
    sheet_row: int | None = None

    @property
    def posting_id(self) -> str:
        return self.id.rsplit(":", 1)[-1]


def read_jobs(path: Path) -> list[Job]:
    if not path.exists():
        return []
    return [Job.model_validate(j) for j in json.loads(path.read_text(encoding="utf-8"))]


def write_jobs(path: Path, jobs: list[Job]) -> Path:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([j.model_dump(mode="json") for j in jobs], indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)
    return path
