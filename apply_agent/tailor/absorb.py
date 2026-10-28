"""Step 0: turn answered inbox questions into confirmed facts, then list the jobs to re-tailor."""
from __future__ import annotations

import asyncio
from datetime import date

from pydantic import BaseModel

from apply_agent.core import llm
from apply_agent.core.profile import load_profile
from apply_agent.core.resume_text import CONFIRMED_FACTS, confirmed_facts, sync_facts_mirror
from apply_agent.core.state import SeenJobs

from .inbox import Inbox

SYSTEM = """You convert {name}'s answer to a resume gap question into ONE fact line for {poss} resume-tailoring
rules. Write it in third person, specific and short, keeping {poss} exact scope and caveats (e.g. "prototype only",
"don't say production"). kind = "confirmed" when the answer says {subj} did it (fully or partly, with the limits stated);
kind = "never_claim" when the answer is no / not really / don't claim. Never add anything {subj} didn't say."""


def system_prompt() -> str:
    return SYSTEM.format(**load_profile().prompt_vars())


class Fact(BaseModel):
    kind: str  # "confirmed" | "never_claim"
    fact: str


def _append(facts: list[tuple[str, str]]) -> None:
    md = CONFIRMED_FACTS.read_text(encoding="utf-8") if CONFIRMED_FACTS.exists() else \
        "# Confirmed facts (learned)\n\n## Confirmed\n\n## Never claim\n"
    lines = md.splitlines()
    for kind, line in facts:
        head = "## Confirmed" if kind == "confirmed" else "## Never claim"
        if head not in lines:
            lines += ["", head]
        i = lines.index(head)
        j = i + 1
        while j < len(lines) and not lines[j].startswith("## "):
            j += 1
        while j > i + 1 and not lines[j - 1].strip():  # insert above the section's trailing blank lines
            j -= 1
        lines.insert(j, f"- {line}")
    CONFIRMED_FACTS.parent.mkdir(parents=True, exist_ok=True)
    CONFIRMED_FACTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    confirmed_facts.cache_clear()
    sync_facts_mirror()


async def _convert(q, model: str) -> tuple[str, str]:
    name = load_profile().first_name
    f = await llm.structured(model, system_prompt(), f"Question: {q.text}\n{name}'s answer: {q.answer}", Fact,
                             effort="low", max_tokens=1500)
    kind = "never_claim" if f.kind.strip().lower().startswith("never") else "confirmed"
    return kind, f"{f.fact.strip()} ({q.id}, {date.today().isoformat()})"


def active_folders(seen: SeenJobs) -> set[str]:
    """Folders (relative to resume/customized/) of tailored jobs not yet applied / closed / expired."""
    return {v["resume_dir"].split("resume/customized/", 1)[-1]
            for v in seen.by_status("customized").values() if v.get("resume_dir")}


def absorb(model: str) -> list[str]:
    """Returns the resume folders (e.g. 'Stripe_SWE') whose questions were answered and that aren't applied yet."""
    inbox = Inbox()
    answered = inbox.answered()
    if not answered:
        return []
    facts = asyncio.run(llm.gather_limited([_convert(q, model) for q in answered], 4))
    good, ok_ids = [], set()
    for q, f in zip(answered, facts):
        if isinstance(f, Exception):
            continue
        good.append(f)
        ok_ids.add(q.id)
    _append(good)
    inbox.mark_absorbed(ok_ids, date.today().isoformat())
    inbox.save()
    folders = sorted({f for q in answered if q.id in ok_ids for f in q.asked_by} & active_folders(SeenJobs()))
    print(f"[absorb] {len(ok_ids)} answers → applicant/facts.md · re-tailor {len(folders)}: {', '.join(folders)}")
    return folders
