# Testing

Unit tests (offline, about 1 second):

```bash
.venv/bin/python -m pytest tests -q
```

Evaluation scripts in this folder (they call real APIs, so they cost a little):

| Script | What it checks |
|---|---|
| `python -m tests.compare_models --run-dir runs/<date> --model deepseek-chat --n 40` | A scoring model's keep/drop agreement with Claude's verdicts, its cost, and cache hit rate |
| `python -m tests.compare_ranking --run-dir runs/<date>` | The same jobs ranked under 5 weight settings (writes `rank_compare.md`) |

What the offline suite covers beyond the stages below:

| File | What it checks |
|---|---|
| `test_applicant_surface.py` | Facts + preferences loading, appending learned facts, scorer prompt building, config loading, extension packets, step 0 |
| `test_profile.py` | `applicant/profile.yaml` validation, the extension's autofill values, settings merge over `config.yaml`, prompts built from the profile, the facts copy for tailoring sessions, `init`, `sync` |
| `test_release_guard.py` | Nothing private is tracked by git, and no shipped file contains your name, email, phone, address or links (see below) |

The suite never reads your real `applicant/`: `conftest.py` points `APPLY_AGENT_APPLICANT` at a copy of `tests/fixtures/applicant/` (a made-up person) before anything is imported.

The manual test plans for each stage follow.

## Discover (job listener)
Run all of them:

```bash
pytest tests -v
```

Live tests hit real APIs, so they are marked `@pytest.mark.live`. Skip them offline:

```bash
pytest tests -v -m "not live"
```

### 1. Smoke check each API by hand (before writing code)

Each command should print a non-zero count.

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/stripe/jobs" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['jobs']))"
```

```bash
curl -s "https://api.lever.co/v0/postings/palantir?mode=json" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))"
```

```bash
curl -s "https://api.ashbyhq.com/posting-api/job-board/notion" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['jobs']))"
```

```bash
curl -s "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings?limit=1" | python3 -c "import json,sys;print(json.load(sys.stdin)['totalFound'])"
```

```bash
curl -s -X POST "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs" -H "Content-Type: application/json" -d '{"appliedFacets":{},"limit":20,"offset":0,"searchText":"software engineer"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['total'])"
```

### 2. Per-source tests (live, one per ATS)

Every source must pass all of these:

| Check | Why |
|---|---|
| Returns at least 1 job for a known-good token | The endpoint and parsing work |
| Every job has non-empty `id`, `title`, `company`, `url`, `source` | Contract holds |
| `id` is identical across two consecutive fetches | Dedupe and `seen_jobs` depend on it |
| `posted_at` is valid ISO 8601 UTC or null, and not in the future | Date parsing (Lever is in ms, Simplify in s, Workday is relative text) |
| `description` has no `<`, `&lt;` or `&amp;` left over | HTML unescape and strip worked (Greenhouse is double-encoded) |
| `url` returns HTTP 200 (check 3 random jobs) | The link is clickable for the tracker |
| An unknown token gives `[]` plus an `errors.jsonl` entry, without raising | One bad company can't kill the run |

Source-specific checks:
- **Greenhouse:** a board with more than 500 jobs parses in under 10 seconds.
- **Lever:** `createdAt` 1786469891368 parses to 2026-08-11 UTC, not the year 58000.
- **Ashby:** jobs with `isListed: false` are excluded.
- **SmartRecruiters:** paging returns `totalFound` items with no duplicates. Each configured token has `totalFound` greater than 0, and a token that returns 0 is flagged as probably wrong.
- **Workday:** paging past 20 works. `"Posted Today"` maps to today, and `"Posted 30+ Days Ago"` maps to null or 30 days ago. The detail call fills in `description` and `posted_at` from `startDate`.
- **Simplify:** inactive or hidden items are dropped. `sponsorship: "Does Not Offer Sponsorship"` maps to `sponsorship_signal: "no"`. A listing whose URL is already fetched from an ATS doesn't appear twice.

### 3. Filter unit tests (offline, table-driven)

Put the cases in `tests/filter_cases.yaml` so they're easy to extend. Each case must have the expected outcome.

| Title / text | Expected |
|---|---|
| `Software Engineer, New Grad (2027)` | keep, SWE |
| `Senior Software Engineer` | drop: seniority |
| `Software Engineer II` | drop: seniority |
| `Software Engineer I` | keep |
| `Associate Product Manager` | keep, PM |
| `Product Marketing Manager` | drop: role |
| `Engineering Manager, Payments` | drop: role |
| `Software Engineering Intern` | drop: internship (unless `include_internships`) |
| location `Palo Alto, CA` | `bay_area=true` |
| location `Remote - US` | `remote_us=true` |
| location `Remote - Canada` | `remote_us=false` |
| location `Seattle, WA` with mode `bay_area_or_remote` | drop: location |
| JD `5+ years of experience` | drop: years (min 5) |
| JD `3+ years, or 1+ with a Master's degree` | keep (min 1) |
| JD `We are unable to sponsor visas for this role` | `sponsorship_signal=no`, drop |
| JD `must be authorized to work without sponsorship now or in the future` | `no`, drop |
| JD with no visa mention | `unknown`, keep |

### 4. End-to-end run (live)

```bash
python -m apply_agent.discover.run --run-dir runs/test
```

- It finishes in under 3 minutes for about 50 companies.
- `01_listed.json` validates against the `Job` model.
- Pick 20 jobs from `listener_drops.jsonl` at random and read the titles. Count any wrong drop as a bug and add it to `filter_cases.yaml`.
- Pick 10 kept jobs and open their URLs. Each one should be a real, open SWE/PM entry-level posting.

## Score (matchmaker)
The goal is to check that the matchmaker's keep/reject decisions and scores agree with the applicant's own judgment.

### 1. Build a labeled set (one time, about 30 minutes)

Create `apply_agent/score/tests/labeled.jsonl` with **30 real postings** and the applicant's verdicts:

```jsonc
{"id": "...", "company": "...", "title": "...", "description": "...", "expected_keep": true, "expected_score_band": "75-89", "note": "why"}
```

Cover these cases:
- 6 clear strong fits (new-grad SWE in AI/LLM, Bay Area). Take some from `resume/customized/submitted/`, since she already chose to apply there.
- 6 weak fits (for example embedded/C++ systems, or data engineering on Spark).
- 4 PM roles (APM programs, plus one "Senior PM" that should fail on level).
- 4 explicit no-sponsorship or US-citizen-only postings, which should fail `sponsorship_ok`.
- 4 postings that say nothing about sponsorship, which should pass `sponsorship_ok`.
- 3 postings that require "3+ years" (fail), plus 1 that says "3+ years or a Master's" (pass).
- 2 Seattle/NYC-only postings, which should fail location in `bay_area_or_remote` mode.

### 2. Accuracy run

```bash
python -m apply_agent.score.run --eval apply_agent/score/tests/labeled.jsonl
```

This prints a confusion matrix plus each disagreement with the model's `needs_reasons`, `alignment` and `gaps`.

Pass bar:
- **Hard-requirement checks: 0 false keeps** on the sponsorship and level cases (a wrongly kept job wastes an application). At most 2 false rejects overall.
- **Score:** at least 80% of jobs land within ±1 band of `expected_score_band`, and every strong fit scores above every weak fit.
- **Stability:** run the 6 strong fits 3 times each. The score spread for any one job is at most 10 points.

If it fails, change `prompt.md` (rubric wording, needs definitions) and re-run. Don't loosen the pass bar.

### 3. Grounding spot-check

For 5 kept jobs, read the `alignment` points and confirm each one cites something that's actually in the resume. A single alignment point that invents a skill counts as a failure. Tighten the prompt, then re-run.

### 4. Mechanics

| Check | How |
|---|---|
| Caching works | From call 2 onward, `cache_read_input_tokens > 0` in `matchmaker_usage.json` |
| Concurrency honored | Logs never show more than `concurrency` requests in flight |
| One failure doesn't stop the run | Inject a job with a 2M-char description. It appears in `errors.jsonl` and the rest complete |
| Seen jobs skipped | A second run on the same `01_listed.json` makes 0 API calls |
| Cost logged | `matchmaker_usage.json` has total input, output and cache tokens and an estimated $ |

## Tailor (ranking + resume tailoring)
### 1. Ranking: compare 5 weight settings

Use one real `02_matched.json` (at least 40 jobs) and a fixed `now`. Run the ranker under each setting:

| # | `match_weight` | `half_life_days` | What it tests |
|---|---|---|---|
| A | 1.0 | n/a | Pure match score (recency ignored) |
| B | 0.8 | 14 | Match-heavy |
| C | **0.7** | **14** | **Default** |
| D | 0.6 | 7 | Recency-heavy |
| E | 0.5 | 3 | Very recency-heavy (should look wrong) |

```bash
python -m tests.compare_ranking --run-dir runs/2026-10-03 --now 2026-10-03
```

`tests/compare_ranking.py` writes `runs/<date>/rank_compare.md`. It has one table per setting (rank, company, title, match, age in days, score) plus a summary of **which jobs enter or leave the top `jobs_per_day` compared with setting C**.

Review it with the applicant and check:
- [ ] Every job she'd apply to first is in C's top `jobs_per_day`.
- [ ] No job with a match score under 65 makes C's top list just because it's fresh.
- [ ] No job with a match score over 85 drops out of C's top list just because it's 2–3 weeks old.
- [ ] The per-company cap holds (at most 2 per company).
- [ ] The results are deterministic: running the same input twice gives an identical ranking.

If a different setting matches her picks better, update `config.yaml → rank` and record why in this file.

Unit tests (`pytest tests`):
- recency is 1.0 at age 0, 0.5 at `half_life`, and 0.25 at twice `half_life`
- `posted_at=None` gives recency 0.5
- a future `posted_at` is clamped to age 0
- ties are broken by match, then date, then id

### 2. `/customize-batch` integration (run together with the applicant)

Run it on **3 jobs first**, not 20:

```bash
python -m apply_agent.tailor.run --run-dir runs/2026-10-03 --jobs 3
```

For each job, check:

| Check | Pass |
|---|---|
| Folder `resume/customized/{YYYY-MM-DD}/{Company}_{ROLE}/` has `.tex`, `.pdf`, `changes.txt`, `jd.txt`, `status.json` | all present |
| `pdfinfo … | grep Pages` | `Pages: 1` |
| Rendered non-blank line count (per `customization.md`) | ≤ the base resume's count |
| `diff -u <base>.tex <custom>.tex` | only bullet, heading or skills text changed |
| Every number in `\textbf{}` in the base still appears unchanged | yes |
| No new skill, tool or bullet that isn't in the base or the pre-approved facts | The applicant confirms |
| Every `changes.txt` entry matches a real diff hunk | yes |
| `status.json.questions` are concrete and answerable, and there are fewer than 5 | The applicant confirms |
| No session hung waiting for input | all exited within the 15-minute timeout |

Then run all 20 with `concurrency: 4`. Confirm the wall time is under about 30 minutes, nothing from different jobs got mixed up across folders, and `customize_usage.json` totals look reasonable.

**Regression set:** keep 3 already-approved folders from `resume/customized/submitted/` (for example Notion_SWE, Scale_SWE and Salesforce_PM). Re-run `/customize-batch` on their JDs and compare. The new edits should be **no more aggressive** than the approved ones, and anything extra is a red flag.

### 3. Question inbox and learning

| Check | Pass |
|---|---|
| Two sessions raise the same gap ("React at Acme?") | `questions.md` has **one** entry with both jobs in `Asked by` |
| A session raises a question already settled in Pre-approved edits | That counts as a bug in `/customize-batch`; tighten its prompt |
| Answer one question with "yes", then run step 0 | The fact appears in `customize.md`, the question moves to **Answered**, and only the unapplied jobs in `Asked by` are re-tailored |
| Answer one with "no" | A **Never claim** line is added, and the re-tailored resumes don't imply it |
| Run the same JD again after answering | 0 questions about that topic |
| Leave all answers blank | Step 0 does nothing; no sessions are launched |

### 4. Fact check (warnings)

- Change one bolded number in a customized `.tex` by hand. The warning `number changed` appears, the job still counts as `ok`, and SUMMARY.md shows ⚠️.
- Add "Kubernetes" to a Skills line by hand. The warning `new term: Kubernetes` appears.
- Run on 5 normal outputs. **At most 1 false warning in total**, otherwise you'll learn to ignore it. Add recurring false positives (for example "LLM", "AI") to an allowlist in config.

