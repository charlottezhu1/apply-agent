# applicant/

Everything stable about you. This folder is gitignored: it never leaves your machine unless you copy it.

| File | What it is | Who writes it |
|---|---|---|
| `profile.yaml` | Name, contact, education, work authorization, EEO answers, and `settings:` (your overrides of `config.yaml`) | You. The pipeline only reads it. |
| `preferences.md` | What the job scorer should avoid and prefer | You |
| `facts.md` | Facts you confirmed beyond your resume, and things never to claim | The pipeline appends here when you answer questions; you can edit it too |

After editing, run `python -m apply_agent.sync`. It refreshes the Chrome extension's autofill values and the copy of
`facts.md` the resume tailoring sessions read. The daily run does the same at the start.

Back it up by making this folder a private git repo (`git init` here); the daily run commits it for you.
