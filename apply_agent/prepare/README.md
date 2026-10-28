# Submission preparer: answers, tracker rows, autofill packets

For each of today's `jobs_per_day` jobs, this stage prepares everything except the click on Submit:

1. **Application answers.** It finds the free-text questions on each application (for example "Why do you want to work at X?") and drafts answers with Claude, grounded in that job's tailored resume.
2. **Tracker rows.** It writes Company, Job Title, Job Link and Posted Date into the **AUTOMATED** tab of the Google Sheet you set up under `sheet:` in your settings.
3. **Autofill packet.** It writes the answers and resume path where the Chrome extension can read them, so they fill in on that job's application page.
4. **Checklist.** It writes `runs/<date>/SUMMARY.md`, the one page the pop-up opens.

- **Input:** `runs/<date>/03_top.json` (jobs with `customize_status` of `ok` or `needs_answers`; jobs marked `failed` are listed in SUMMARY.md but skipped otherwise)
- **Output:** `runs/<date>/04_packets.json`, `chrome_extension/generated/packets.json`, `runs/<date>/SUMMARY.md`, and rows in the sheet

---

## 1. Find each application's questions

| Source | How to get the questions |
|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true` → `questions[]` with `label`, `required`, `fields[].type` (`input_text`, `textarea`, `multi_value_single_select`, `input_file`, …). Verified working. |
| Lever | Fetch `https://jobs.lever.co/{token}/{id}/apply` and parse the custom-question cards in the HTML (`.application-question` → label text + `textarea`/`input`). |
| Ashby, SmartRecruiters, Workday, Simplify-only | No public questions API. Use the **default set** below. |

**Sort each question into one of three buckets:**
- **Profile:** name, email, phone, links, location, school, work authorization, sponsorship, EEO. **Skip these**, since the extension already fills them from `applicant/profile.yaml`.
- **Needs the applicant:** salary expectations, start date, "have you worked here before", referral name, anything legal. Don't generate an answer. List these in SUMMARY.md under "fill by hand".
- **Essay:** any `textarea`, or `input_text` whose label is a question about motivation, experience, a project, or "anything else". **Generate an answer.**

**No default essays:** if the form's questions can't be fetched, or it has no essay questions, generate nothing.
- Cover letter. Generate one **only** if `config.submission.cover_letter: true`.

## 2. Generate answers

One Claude call per (job, essay question), run in parallel. Use the same SDK, model, effort and concurrency settings as the matchmaker, with plain-text output.

Context for each call:
- the **customized resume for this job** as plain text (`pdftotext <resume_pdf> -`), not the base resume
- the full JD
- the matchmaker's `alignment` points
- the question text and any character or word limit (from the label, for example "(max 500 characters)")

Rules for the prompt:
- Write in the first person in the applicant's voice: plain, specific and confident, with no clichés ("I am passionate about…").
- **Use only facts in the resume and the JD.** Don't invent company facts, products, metrics or experiences. A reason for wanting the company must come from what the JD says the team or product does.
- Aim for 100–150 words unless the question sets a limit. Always stay under any limit.
- Cover letter: 3 short paragraphs, under 250 words.

Write each answer into `job.answers[question_label]`. The applicant reviews every answer in SUMMARY.md before using it.

## 3. Write to the Google Sheet

### Google Sheet setup (one time)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Google Sheets API** and the **Google Drive API**.
2. Go to **IAM & Admin → Service Accounts → Create**, then **Keys → Add key → JSON**. Save the key to the path in `.env → GOOGLE_SERVICE_ACCOUNT_JSON`. Never commit it.
3. Open your tracker sheet, click **Share**, and add the service account's email (`…@….iam.gserviceaccount.com`) as an **Editor**.
4. Copy the sheet id from its URL (`docs.google.com/spreadsheets/d/<SHEET_ID>/edit`) into `config.yaml → sheet.id`. Opening by id is more reliable than opening by name.
5. Test the connection:

```bash
python -m apply_agent.prepare.sheets --check
```

It should print the AUTOMATED tab's header row.

### Write logic (`sheets.py`)

```python
gc = gspread.service_account(filename=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
ws = gc.open_by_key(cfg.sheet.id).worksheet(cfg.sheet.tab)        # "AUTOMATED"
```

- **Find columns by header name, not position.** Read row 1 and locate `Company`, `Job Title`, `Job Link` and `Posted Date`. If any of them is missing, stop with a clear error. Never rearrange the sheet.
- **Dedupe on Job Link.** Skip any job whose `url` is already in the Job Link column.
- Write new rows in rank order with one `ws.append_rows(rows, value_input_option="USER_ENTERED")` call, which saves quota. Fill only those four columns and leave the rest blank for the applicant. Use `YYYY-MM-DD` in Pacific time for Posted Date, and leave it blank if it's unknown.
- Record each job's row number in `job.sheet_row`.
- `--dry-run` prints the rows instead of writing them.

## 4. Autofill packets for the Chrome extension

The extension can only read files inside its own folder, so this stage writes `chrome_extension/generated/packets.json` (gitignored). It holds one packet for every tailored job that isn't applied, closed or expired, so backlog jobs keep working:

```json
[{"match": ["7012345"], "company": "Stripe", "title": "Software Engineer, New Grad", "folder": "Stripe_SWE",
  "resume_pdf": "/path/to/apply_agent/resume/customized/2026-10-04/Stripe_SWE/Stripe_SWE_Alex_Resume.pdf",
  "answers": {"Why do you want to work at Stripe?": "…"}}]
```

Drafted answers are also saved in each resume folder as `answers.json`, and questions to fill by hand as `fill_by_hand.json`.

Use the posting id as the `match` key, not the full URL. The page where you fill in the application is often different from `job.url` (Greenhouse forms are frequently embedded, as in `job-boards.greenhouse.io/embed/job_app?for=stripe&token=7012345`).

The extension reads the file fresh on every fill, so **no reload is needed** after a run. See [chrome_extension/README.md](../../chrome_extension/README.md#per-job-answers-from-the-pipeline).

## 5. `SUMMARY.md`

This is the only page you need to open each morning. It has these sections, in order:

1. **Header:** `6 new · 3 open questions · 0 failures · $12.40 spent · Questions per resume: 0.8 this week (1.9 last week)`
2. **Questions for you:** the number of open items, with a link to `resume/questions.md`. Answer them there, and they're absorbed on the next run.
3. **New today:** one block per job, in rank order (format below). A ⚠️ line appears only if the fact check flagged something.
4. **Backlog:** jobs tailored on earlier days that aren't applied yet, closed or expired yet. One line each: rank, company, title, apply link, resume path and days left before it expires.
5. **Closed since yesterday:** postings that disappeared. Don't apply to these.
6. **Failures:** the step and the reason, with the log path.

Each job in "New today" looks like this:

```markdown
## 3. Stripe · Software Engineer, New Grad · match 84 · posted 2026-09-30
- Apply: https://…
- Resume: resume/customized/2026-10-04/Stripe_SWE/Stripe_SWE_Alex_Resume.pdf  (status: needs_answers)
- ⚠️ new term: Kubernetes   (only shown when the fact check flags something)
- Open resume questions: Q-0007, Q-0008
- Fill by hand: salary expectation; start date
- Answers (review before pasting):
  - **Why Stripe?** …
- Sheet row: 14
- When you've applied: move the folder to `resume/customized/submitted/`
```

---

## Code layout

```
apply_agent/prepare/
├── questions.py   # fetch + classify questions per source
├── answers.py     # Claude calls
├── sheets.py      # gspread; CLI: --check
├── applied.py     # "I applied": move folder to submitted/, mark applied, add sheet row
└── run.py         # answers + packets (build_packets → chrome_extension/generated/packets.json); CLI: --run-dir, --dry-run, --no-sheet
```

## Tests

- **Questions:** for 3 live Greenhouse jobs, `?questions=true` gets sorted into the buckets correctly. Name and email go to profile, "sponsor" goes to profile, and the textarea questions go to essay.
- **Answers:** for 5 answers, the applicant checks that every factual claim appears in the resume or JD. Any invented claim fails the test.
- **Sheet:** run twice on the same `03_top.json`. The second run adds 0 rows. Then add a test column to the sheet and confirm nothing shifts.
- **Packets:** open one Greenhouse application in Chrome, click Autofill, and confirm the essay fields fill in with that job's answers and not another job's.
