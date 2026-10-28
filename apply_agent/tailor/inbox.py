"""resume/questions.md: one shared inbox of gap questions. Sessions never block; the applicant answers here."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from apply_agent.core import llm
from apply_agent.core.resume_text import RESUME

INBOX = RESUME / "questions.md"
HEADER = """# Question inbox

Gap questions from automated resume tailoring. **Answer after `- Answer:`** (short is fine: "yes", "no — prototype only").
The next run turns each answer into a confirmed fact (`applicant/facts.md`) and re-tailors the resumes that asked.
"""


@dataclass
class Question:
    id: str
    text: str
    asked_by: list[str] = field(default_factory=list)
    first_asked: str = ""
    answer: str = ""
    answered_on: str = ""
    absorbed_on: str = ""

    def render(self) -> str:
        head = f"## {self.id}"
        if self.absorbed_on:
            head += f" (answered {self.answered_on or self.absorbed_on}, absorbed {self.absorbed_on})"
        return (f"{head}\n{self.text}\n- Asked by: {', '.join(self.asked_by)} (first asked {self.first_asked})\n"
                f"- Answer: {self.answer}\n")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


class Inbox:
    def __init__(self, path: Path = INBOX):
        self.path = path
        self.open: list[Question] = []
        self.done: list[Question] = []
        if path.exists():
            self._parse(path.read_text(encoding="utf-8"))

    def _parse(self, md: str) -> None:
        section = None
        for block in re.split(r"(?m)^(?=# |## )", md):
            if block.startswith("# Open"):
                section = self.open
                continue
            if block.startswith("# Answered"):
                section = self.done
                continue
            if not block.startswith("## ") or section is None:
                continue
            lines = block.strip().splitlines()
            m = re.match(r"## (Q-\d+)(?: \(answered ([\d-]+), absorbed ([\d-]+)\))?", lines[0])
            if not m:
                continue
            q = Question(id=m.group(1), text="", answered_on=m.group(2) or "", absorbed_on=m.group(3) or "")
            body, in_answer = [], False
            for ln in lines[1:]:
                if in_answer:  # anything after "- Answer:" belongs to the answer
                    q.answer = (q.answer + " " + ln.strip()).strip()
                elif ln.startswith("- Asked by:"):
                    mm = re.match(r"- Asked by:\s*(.*?)\s*\(first asked ([\d-]+)\)", ln)
                    if mm:
                        q.asked_by = [x.strip() for x in mm.group(1).split(",") if x.strip()]
                        q.first_asked = mm.group(2)
                elif ln.startswith("- Answer:"):
                    q.answer = ln[len("- Answer:"):].strip()
                    in_answer = True
                elif not ln.startswith("- "):
                    body.append(ln)
            q.text = "\n".join(body).strip()
            section.append(q)

    def save(self) -> None:
        parts = [HEADER, "# Open\n"]
        parts += [q.render() for q in self.open] or ["_No open questions._\n"]
        parts += ["# Answered\n"] + [q.render() for q in self.done]
        self.path.write_text("\n".join(parts), encoding="utf-8")

    def next_id(self) -> str:
        nums = [int(q.id[2:]) for q in self.open + self.done]
        return f"Q-{(max(nums) + 1) if nums else 1:04d}"

    def answered(self) -> list[Question]:
        return [q for q in self.open if q.answer]

    def mark_absorbed(self, ids: set[str], today: str) -> None:
        for q in [q for q in self.open if q.id in ids]:
            q.answered_on = q.answered_on or today
            q.absorbed_on = today
            self.open.remove(q)
            self.done.append(q)

    async def add(self, asked: dict[str, list[str]], today: str, model: str) -> dict[str, list[str]]:
        """asked: {job_folder: [question, ...]} → {job_folder: [Q-ids]}. Merges duplicates into existing entries."""
        by_norm = {_norm(q.text): q for q in self.open}
        result: dict[str, list[str]] = {f: [] for f in asked}
        pending: list[tuple[str, str]] = []
        for folder, qs in asked.items():
            for text in qs:
                q = by_norm.get(_norm(text))
                if q:
                    self._attach(q, folder, result)
                else:
                    pending.append((folder, text))
        if pending and self.open:
            mapping = await self._semantic_match([t for _, t in pending], model)
        else:
            mapping = [None] * len(pending)
        new_by_norm: dict[str, Question] = {}
        for (folder, text), existing in zip(pending, mapping):
            q = next((q for q in self.open if q.id == existing), None) or new_by_norm.get(_norm(text))
            if q is None:
                q = Question(id=self.next_id(), text=text.strip(), first_asked=today)
                self.open.append(q)
                new_by_norm[_norm(text)] = q
            self._attach(q, folder, result)
        return result

    @staticmethod
    def _attach(q: Question, folder: str, result: dict) -> None:
        if folder not in q.asked_by:
            q.asked_by.append(folder)
        if q.id not in result[folder]:
            result[folder].append(q.id)

    async def _semantic_match(self, new: list[str], model: str) -> list[str | None]:
        class Match(BaseModel):
            existing_id: list[str]  # one per new question, "" when it is genuinely new

        existing = "\n".join(f"{q.id}: {q.text}" for q in self.open)
        news = "\n".join(f"{i}: {t}" for i, t in enumerate(new))
        try:
            m = await llm.structured(
                model,
                "You deduplicate questions. Two questions are the same if a single answer would settle both.",
                f"<existing>\n{existing}\n</existing>\n<new>\n{news}\n</new>\n"
                f"Return existing_id: a list of exactly {len(new)} strings, in order. For each new question give "
                f"the id of the existing question it duplicates, or \"\" if none.",
                Match, effort="low", max_tokens=2000)
            ids = {q.id for q in self.open}
            out = [x if x in ids else None for x in m.existing_id]
            return (out + [None] * len(new))[:len(new)]
        except Exception:
            return [None] * len(new)
