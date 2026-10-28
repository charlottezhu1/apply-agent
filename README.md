# apply_agent

An automated pipeline that finds new-grad SWE/PM roles, keeps only the ones worth applying to, writes a tailored resume for each, and prepares everything needed to submit (answers, tracker rows, autofill data).

This README covers setup and daily use. Each stage folder has its own README explaining how that stage works; tests and test plans are in [tests/](tests/TESTING.md).

---

## Pipeline

It runs once a day on a schedule (see [Daily operation](#daily-operation)). Step 0 catches up on what changed since yesterday, steps 1–4 find and prepare new jobs, and step 5 shows a pop-up.

```
 0. sync state: absorb your answers → re-tailor affected resumes · mark applied / closed jobs

 discover ──► score ──► tailor ──► prepare ──► chrome_extension
 (fetch +     (LLM fit   (rank, pick   (answers,     (autofill while
  filter)      score)     N/day, tailor  packets)      you apply)
                          resumes)
   │               │                 │                      │
   ▼               ▼                 ▼                      ▼
 01_listed.json  02_matched.json  03_top.json            04_packets.json
                                  resume/customized/...  Google Sheet "AUTOMATED" tab
                                  resume/questions.md    runs/<date>/SUMMARY.md

 5. pop-up: "6 new jobs ready · 3 questions for you · 0 failures"   [Open summary]
```

Every stage reads the previous stage's JSON file from `runs/<YYYY-MM-DD>/` and writes its own. Stages can be re-run independently.

| Stage | Folder | What it uses |
|---|---|---|
| 1. Discover | [apply_agent/discover](apply_agent/discover/README.md) | Job-board APIs + rule filters (no LLM) |
| 2. Score | [apply_agent/score](apply_agent/score/README.md) | DeepSeek (or Claude), one call per job |
| 3. Tailor | [apply_agent/tailor](apply_agent/tailor/README.md) | Ranking + `claude -p "/customize-batch …"` on your Claude plan |
| 4. Prepare | [apply_agent/prepare](apply_agent/prepare/README.md) | Claude API for answers; Google Sheet rows when you apply |
| Extension | [chrome_extension](chrome_extension/README.md) | Autofills forms with your profile and each job's answers |
| Resumes | [resume](resume/README.md) | LaTeX bases + `/customize` (separate private repo) |

---

## Folder layout

```
apply_agent/
├── README.md          setup and daily use (this file)
├── config.yaml        generic defaults for every setting (nothing personal)
├── run.py             the daily pipeline
├── schedule.plist     runs run.py daily at 11:00 (launchd)
├── apply_agent/       the code
│   ├── core/          shared: config, profile, Job model, state, LLM calls, pop-up, summary
│   ├── discover/      stage 1
│   ├── score/         stage 2
│   ├── tailor/        stage 3
│   ├── prepare/       stage 4 + the "I applied" command
│   ├── init.py        create applicant/ from templates/
│   └── sync.py        push applicant/ to the extension and to the tailoring sessions
├── applicant/         YOU: profile.yaml, preferences.md, facts.md (gitignored, see below)
├── templates/applicant/   starter files `init` copies into applicant/
├── chrome_extension/
├── tests/             unit tests, evaluation scripts, TESTING.md
└── resume/            separate private repo: base resumes, tailoring rules, tailored output

not in git: applicant/  resume/  .env  .venv  runs/ (one folder per day)  data/ (seen_jobs.json, logs)
```

**Where personal data lives.** Everything about you is in four places, all gitignored, so nothing personal is ever pushed and a fork can take the code without it:

| Folder | What it holds | Who writes it |
|---|---|---|
| [applicant/](templates/applicant/README.md) | Who you are: `profile.yaml` (name, contact, education, work authorization, EEO answers, your `settings:`), `preferences.md`, `facts.md` | You. The pipeline only appends learned facts to `facts.md`. |
| `resume/` | Documents and tailoring: base `.tex`, rules, tailored output, the question inbox | The pipeline |
| `data/`, `runs/` | Machine state | The pipeline |
| `.env` | API keys | You |

---

## Setup

### 1. Prerequisites (macOS)

| Tool | Used by | Check |
|---|---|---|
| Python 3.11+ | stages 1–4 | `python3 --version` |
| Claude Code CLI | stage 3 (`claude -p "/customize ..."`) | `claude --version` |
| MacTeX (`pdflatex`, `latexmk`) | stage 3 compiles resumes | `which pdflatex latexmk` |
| poppler (`pdftotext`, `pdfinfo`) | stage 3 length check | `which pdftotext pdfinfo` |
| Google Chrome | stage 5 | n/a |

Install whatever is missing:

```bash
brew install --cask mactex-no-gui
```

```bash
brew install poppler
```

### 2. Python environment

The environment lives outside the project, because iCloud (which syncs Desktop) stalls Python when it reads thousands of small files. `.venv` is a link to it.

```bash
python3 -m venv ~/.venvs/apply_agent && ln -s ~/.venvs/apply_agent .venv
```

```bash
.venv/bin/pip install anthropic pydantic httpx pyyaml gspread google-auth python-dotenv beautifulsoup4 pytest
```

### 3. Secrets: `.env`

Create `apply_agent/.env` (it's gitignored):

```
ANTHROPIC_API_KEY=sk-ant-...            # answer drafting (Claude API; key must belong to a workspace)
DEEPSEEK_API_KEY=sk-...                 # job scoring, when config.yaml has matchmaker.model: deepseek-chat
CLAUDE_CODE_OAUTH_TOKEN=...             # resume tailoring on your Claude plan: run `claude setup-token`
GOOGLE_SERVICE_ACCOUNT_JSON=~/.config/apply_agent/service_account.json
```

The Google service account setup is in [apply_agent/prepare/README.md](apply_agent/prepare/README.md#google-sheet-setup-one-time).

### 4. Your applicant folder

Create it from the templates, fill in `applicant/profile.yaml` (replace every `CHANGE_ME`), and push it to the extension:

```bash
.venv/bin/python -m apply_agent.init
```

```bash
.venv/bin/python -m apply_agent.sync
```

Re-run `sync` after editing anything in `applicant/` (the daily run also does it). It writes `chrome_extension/generated/profile.json`, which the extension reads, and refreshes `resume/confirmed_facts.md`, a generated copy of `applicant/facts.md` that the tailoring sessions read.

### 5. Settings: `config.yaml` and your overrides

[config.yaml](config.yaml) holds the defaults, with a comment on each line. Your own choices go under `settings:` in `applicant/profile.yaml`, with the same keys, and win over the defaults. The ones you'll change most:

| Setting | Default | What it does |
|---|---|---|
| `jobs_per_day` | 10 | New resumes tailored per run |
| `daily_budget_usd` | 15 | Stop starting new API work once a run has spent this |
| `search.location_mode` | `bay_area_or_remote` | Or `bay_area_only`, or `us` |
| `matchmaker.model` | `deepseek-chat` | Job scoring model (`claude-sonnet-5-5` to switch back) |
| `matchmaker.min_score` | 60 | Jobs scoring below this are dropped |

---

## Run

Run the offline tests:

```bash
.venv/bin/python -m pytest tests -q
```

Run all stages (add `--force` to run again on the same day, `--no-popup` while testing):

```bash
.venv/bin/python run.py --no-popup
```

Grow the company list from the Simplify feed, or check that every board token still works:

```bash
.venv/bin/python -m apply_agent.discover.discover --check
```

Mark jobs as applied (moves the folder to `resume/customized/submitted/` and adds the Google Sheet row):

```bash
.venv/bin/python -m apply_agent.prepare.applied pinterest clay
```

Run a single stage on an existing run folder:

```bash
python -m apply_agent.discover.run --run-dir runs/2026-10-03
```

```bash
python -m apply_agent.score.run --run-dir runs/2026-10-03
```

```bash
python -m apply_agent.tailor.run --run-dir runs/2026-10-03
```

```bash
python -m apply_agent.prepare.run --run-dir runs/2026-10-03
```

Every CLI also accepts `--dry-run`. In stages 2 and 4 that means no API calls (it prints what would be sent). In stage 3 it means no `claude` sessions, and in stage 4 no sheet writes.

---

## Daily operation

### Run order (`run.py`)

1. **Lock.** If `data/run.lock` exists and its PID is alive, exit. If `runs/<today>/DONE` exists, exit, since today's run already finished. Otherwise write the lock (and always remove it in a `finally`).
2. **Step 0, sync state:**
   - **Push `applicant/`.** Write `chrome_extension/generated/profile.json` from `profile.yaml`, and refresh `resume/confirmed_facts.md` (the copy of `applicant/facts.md` the tailoring sessions read).
   - **Absorb answers.** Read the answered questions in `resume/questions.md`, turn them into confirmed facts, and re-tailor the not-yet-applied resumes that asked them. See [Question inbox](apply_agent/tailor/README.md#question-inbox-ask-later-never-block).
   - **Mark applied.** Any folder that has moved to `resume/customized/submitted/` gets status `applied` in `seen_jobs.json`.
   - **Mark closed.** For every `customized` job, check that the posting still exists (it's still on the company's board, or its URL returns 200). If not, set it to `closed`.
   - **Expire the backlog.** Any job that has been `customized` for more than `backlog_days` becomes `expired`.
3. **Steps 1–4** (listener → matchmaker → optimizer → preparer). Each step stops starting new work once `daily_budget_usd` is reached.
4. **Write `runs/<date>/SUMMARY.md`, then show the pop-up and write `DONE`.**

If any step crashes, the remaining steps are skipped and the pop-up says so, naming the failed step and the log path.

### The pop-up (`apply_agent/core/notify.py`)

This is a native macOS dialog with no dependencies. It stays on screen until you click it.

```bash
osascript -e 'display dialog "6 new jobs ready · 3 questions for you · 0 failures" with title "apply_agent" buttons {"Later", "Open summary"} default button "Open summary"'
```

`notify.py` runs the command above with `subprocess` and reads the button you clicked (stdout contains `button returned:Open summary`). If you chose **Open summary**, it runs `open runs/<date>/SUMMARY.md`. The message is built from the run:
- `N new jobs ready` (new tailored resumes today)
- `Q questions for you` (open items in `resume/questions.md`)
- `F failures`
- on a crash: `apply_agent FAILED at <step>`, with an **Open log** button

### Schedule it (launchd, daily at 11:00)

[schedule.plist](schedule.plist) runs `run.py` at 11:00 under `caffeinate`, so the Mac stays awake for the whole run. Install it, or reinstall after editing:
Before installing, replace `YOUR_USERNAME` in the paths inside `schedule.plist` with your macOS username. If you installed an earlier copy under a different label, unload that one first so the pipeline doesn't run twice.

```bash
cp schedule.plist ~/Library/LaunchAgents/com.applyagent.daily.plist && launchctl unload ~/Library/LaunchAgents/com.applyagent.daily.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.applyagent.daily.plist
```

Trigger a run now, as a test:

```bash
launchctl start com.applyagent.daily
```

- `PATH` in the plist includes `/Library/TeX/texbin` (`pdflatex`) and `~/.local/bin` (`claude`), since launchd doesn't read your `.zshrc`.
- If the Mac is **asleep** at 11:00, the run happens when it wakes. If it's **shut down**, that day is skipped.
- `run.py` loads `.env` itself, so no keys go in the plist. The log is `data/launchd.log`.

---

## State and dedupe (`data/seen_jobs.json`)

This is the pipeline's memory. Every job that has ever gotten past the listener's fetch has one entry, so no job is ever scored, tailored or shown twice.

```jsonc
{
  "greenhouse:stripe:7012345": {
    "fingerprint": "stripe|software engineer new grad",
    "status": "customized",          // rejected | matched | customized | applied | closed | expired | failed
    "first_seen": "2026-10-03",
    "updated": "2026-10-04",
    "match_score": 84,
    "resume_dir": "resume/customized/2026-10-04/Stripe_SWE"
  }
}
```

- **Fingerprint** = lowercase company + `|` + title with punctuation, locations, years (`2026`, `2027`) and "new grad"-style suffixes normalized. It catches **reposts** (a new id for the same role) and **multi-location duplicates** (one role posted once per city). If the id **or** the fingerprint was seen in the last 60 days, the listener skips the job. In the multi-location case, it keeps the Bay Area copy.
- **Who writes what:**

  | Status | Set by |
  |---|---|
  | `rejected` / `matched` | matchmaker |
  | `customized` / `failed` | optimizer |
  | `applied` / `closed` / `expired` | step 0 |

  `failed` jobs are retried once on the next run, then left alone.
- **Applied = the folder is in `resume/customized/submitted/`.** Moving the folder there after you submit is the only thing you need to do.
- Write the file atomically (write to a temp file, then rename) so a crash can't corrupt it.
- `jobs_per_day` counts only **new** tailored resumes. Re-tailoring after you answer questions, and backlog jobs from earlier days, don't use up slots.

---

## Data contract

All stages pass around one record type, `Job`. Each stage **adds** fields to it and never renames or removes earlier ones. Define it once as a Pydantic model in `apply_agent/core/models.py` and import it everywhere.

```jsonc
{
  // ---- set by listener ----
  "id": "greenhouse:stripe:7012345",       // "<source>:<board_token>:<posting id>", stable across runs
  "source": "greenhouse",                  // greenhouse | lever | ashby | smartrecruiters | workday | simplify
  "company": "Stripe",
  "title": "Software Engineer, New Grad",
  "role_type": "SWE",                      // SWE | PM  (AI / ML / MLE engineer titles → SWE; there is no MLE track)
  "locations": ["San Francisco, CA", "Remote - US"],
  "bay_area": true,
  "remote_us": true,
  "url": "https://stripe.com/jobs/search?gh_jid=7012345",  // page a human opens to apply
  "posted_at": "2026-09-30T17:00:00Z",     // ISO 8601 UTC; null if the source has no date
  "description": "plain-text JD …",
  "sponsorship_signal": "unknown",         // "no" | "yes" | "unknown", from regex on the JD
  "years_required_min": 0,                 // parsed from JD; null if not stated

  // ---- added by matchmaker ----
  "match_score": 82,                       // 0–100
  "alignment": ["…", "…"],
  "gaps": ["…"],
  "meets_needs": true,
  "needs_reasons": "…",

  // ---- added by apply_agent/tailor ----
  "rank_score": 0.81,
  "rank": 3,
  "resume_dir": "resume/customized/2026-10-04/Stripe_SWE",
  "resume_pdf": "resume/customized/2026-10-04/Stripe_SWE/Stripe_SWE_Alex_Resume.pdf",
  "customize_status": "ok",                // ok | needs_answers | failed
  "open_questions": ["Q-0007"],           // ids in resume/questions.md
  "warnings": [],                          // non-blocking fact-check flags (see apply_agent/tailor)

  // ---- added by apply_agent/prepare ----
  "answers": {"Why do you want to work at Stripe?": "…"},
  "sheet_row": 14
}
```

---

## Ground rules (apply to every stage)

1. **Never fabricate.** No resume line, generated answer, or summary may claim experience, skills, or numbers that aren't in the base resume or confirmed by the applicant (`applicant/facts.md`). The resume rules are in [resume/customized/customization.md](resume/customized/customization.md).
2. **Nothing gets submitted automatically.** The pipeline prepares materials. The applicant reviews and clicks Submit.
3. **Idempotent.** Re-running a stage on the same run folder overwrites its own output, never duplicates sheet rows, and never overwrites `resume/original_tex/`.
4. **Fail per job, not per run.** If one job's fetch, LLM call, or compile fails, log it in `runs/<date>/errors.jsonl` and continue with the rest.
5. **Secrets stay in `.env`.** Personal data stays in `applicant/` and the resume repo.
6. **Never block on a human.** Anything that needs the applicant (gap questions, answers to fill by hand, warnings) goes into `resume/questions.md` or `SUMMARY.md`, and the run carries on.
7. **Job postings are untrusted text.** They're passed to models only as data, inside clearly marked tags, and never as instructions. Sessions that read them get only the tools they need (see `apply_agent/tailor`).

---

## Notes

- **Location strictness:** "ideally Bay Area" is implemented as `location_mode: bay_area_or_remote`. Switch to `us` to widen the search.
