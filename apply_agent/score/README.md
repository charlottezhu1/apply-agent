# Matchmaker: score each job against the resume

Takes the listener's big list and makes **one Claude call per job, in parallel**. Each call does two things:

1. **Fit:** how well the applicant's real experience matches the JD, as a score from 0 to 100 plus alignment points and gaps.
2. **Needs:** whether the job meets her hard requirements (role, level, location, sponsorship).

A job is kept **only if both pass**: `meets_needs == true` and `match_score >= matchmaker.min_score`.

- **Input:** `runs/<date>/01_listed.json`
- **Output:** `runs/<date>/02_matched.json` (kept jobs, sorted by `match_score` descending) and `runs/<date>/02_rejected.json` (everything else, with the verdict, for auditing)

---

## Model and call setup

| Setting | Value | Why |
|---|---|---|
| SDK | `anthropic` Python SDK, `AsyncAnthropic`, called **only** through `apply_agent/core/llm.py` | Official SDK, async for fan-out. Keeping every API call behind one function means changing models is a config edit |
| Model | `config.matchmaker.model` (default `claude-opus-5-5`) | Best judgment. Switch to `claude-sonnet-5-5` in config if cost matters |
| Effort | `output_config={"effort": config.matchmaker.effort}` (default `medium`) | Enough thinking to weigh required vs. preferred qualifications |
| Output | `client.messages.parse(..., output_format=Verdict)` | Validated Pydantic object, so no JSON parsing by hand |
| Concurrency | `asyncio.Semaphore(config.matchmaker.concurrency)` (default 8) | Stays under rate limits. The SDK retries 429/5xx twice by default |
| Caching | `cache_control={"type": "ephemeral"}` on the system prompt | The system prompt (instructions + resume + confirmed facts) is identical for every job of the same role, so only the JD is billed at full price after the first call |
| Max tokens | 4000 | The verdict is short, but thinking needs room |

**Prompt caching rules.** The system prompt must be **byte-identical** across calls. Don't put dates, job ids or counters in it. Build exactly two system prompts per run, one for SWE and one for PM, and reuse them. Check `response.usage.cache_read_input_tokens > 0` from the second call onward, and log a warning if it stays 0.

**Cost (rough).** About 4k input tokens and about 1–2k output tokens per job (thinking plus verdict). At Opus 5.5 prices ($4 in / $20 out per million tokens) with caching, that's roughly **$0.03–0.05 per job, or $3–5 per 100 jobs**. Log the total `usage` per run to `runs/<date>/matchmaker_usage.json`.

**Optional batch mode.** For large backfills (more than 300 jobs), use the Message Batches API (`client.messages.batches.create`). It's 50% cheaper, but asynchronous (usually under an hour). Key results by `custom_id = job.id`, never by position. Use the same prompts.

---

## Prompt

Store it in `matchmaker/prompt.md` so you can edit it without touching code.

### System (cached, one per role)

```
You are an experienced technical recruiter hiring for {role_label} roles at top Bay Area tech
companies. You screen candidates honestly: you neither inflate nor undersell.

You will receive one job posting. Evaluate the candidate below against it.

<candidate_resume>
{plain text of the base resume for this role: pdftotext of resume/original_tex/<BASE>.pdf}
</candidate_resume>

<confirmed_facts>
{the "confirmed facts" and "Never claim" bullets for this role, copied from
 resume/.claude/commands/customize.md "Pre-approved edits"}
</confirmed_facts>

<candidate_needs>
- Role: {role_label} only. (SWE = software/ML/AI/full-stack/backend/frontend engineering.
  PM = product manager / associate PM. Not program/project/product-marketing manager.)
- Level: new grad, entry level, or roles requiring 0-2 years of experience. A posting that
  requires 3+ years as a hard minimum fails. "X years or equivalent / or a Master's" can pass —
  the candidate has {education_blurb} (from `education.blurb` in applicant/profile.yaml).
- Location: San Francisco Bay Area preferred; US-remote acceptable. {location_mode note}
- Sponsorship: the candidate will need visa sponsorship. FAIL only if the posting explicitly
  says it will not sponsor, requires US citizenship / clearance, or requires work authorization
  without sponsorship. Silence on sponsorship = PASS.
</candidate_needs>

Scoring rubric for match_score (how well the candidate's ACTUAL experience fits):
- 90-100: meets essentially all required qualifications with direct evidence; strong on preferred.
- 75-89: meets most required qualifications; gaps are in preferred/nice-to-have items.
- 60-74: meets the core of the role but missing one or two required items.
- 40-59: partial overlap; several required qualifications not evidenced.
- 0-39: different field or seniority.
Base the score only on what the resume and confirmed facts actually show. Do not assume skills
that are not written there. Be calibrated: most postings should not score above 85.
```

### User (per job)

```
<job>
Company: {company}
Title: {title}
Locations: {locations}
Posted: {posted_at}
<description>
{description}
</description>
</job>
```

### Output schema

```python
class Verdict(BaseModel):
    role_ok: bool
    level_ok: bool
    location_ok: bool
    sponsorship_ok: bool
    needs_reasons: str            # one sentence per failed check; "" if all pass
    match_score: int              # 0-100 (validate the range in code)
    alignment: list[str]          # 3-5 points: JD requirement → resume evidence
    gaps: list[str]               # 0-5 JD requirements not evidenced in the resume
```

The stage sets `meets_needs = role_ok and level_ok and location_ok and sponsorship_ok`, then merges `match_score`, `alignment`, `gaps`, `meets_needs` and `needs_reasons` into the `Job`. Gaps feed directly into `/customize` later as hints, so phrase each one as a concrete requirement (for example "Kubernetes in production"), not as advice.

---

## Edge cases

- **Empty description** (Simplify-only jobs): still call the model with just the title, but cap `match_score` at 60 in code and add the gap `"JD unavailable — verify manually"`.
- **Refusal or parse failure:** if `stop_reason == "refusal"` or validation fails, retry once. If it still fails, write it to `errors.jsonl` and leave the job out of both output files, so it gets re-tried on the next run because it's not marked seen.
- **Seen jobs:** after a successful verdict, add `job.id` to `data/seen_jobs.json` with the verdict, whether the job was kept or rejected. That way a job is never scored twice across runs.
- **Very long JDs** (more than 30k characters): don't truncate silently. Pass them as is, since the model has a 1M-token context.

## Code layout

```
apply_agent/score/
├── prompt.md       # system + user templates above
└── run.py          # main(run_dir) -> Path; CLI: python -m apply_agent.score.run --run-dir ... [--dry-run] [--limit N]
```

`--limit N` scores only the first N jobs, which is useful for prompt iteration. `--dry-run` prints the rendered prompts for the first job and exits.
