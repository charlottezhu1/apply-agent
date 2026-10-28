---
description: Add a job you found yourself. Scores it, and if it passes, tailors the resume and sets up the extension.
argument-hint: <apply-url> <paste full JD text right after> [--min-score 65] [--force]
---

Add one job to the apply_agent pipeline. Run everything from the project folder (the one containing `run.py`).

$ARGUMENTS

1. The **first word** is the apply-page URL. Everything after it is the job description. Words like `--min-score 65`, `--force`, `--company X`, `--title Y`, `--role swe|pm` at the very start or end are flags; pass them through. If there is no URL or no JD, ask for it and stop.
2. Write the JD text exactly as pasted (no summarizing) to a scratchpad file, e.g. `jd_add.txt`, using Write.
3. Run it (the tailoring step takes a few minutes, so use a long timeout, up to 600000 ms):

   `.venv/bin/python -m apply_agent.add --url <url> --jd <scratchpad file> <flags>`

4. Report briefly: company and title, the score, whether it passed, and if it was tailored the resume PDF path, any open questions and warnings. If it was rejected, give the score and the main reason, and mention `--force`.
