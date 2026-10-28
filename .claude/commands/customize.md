---
description: Customize a base resume for a specific job (company + role + JD). Runs the resume repo's /customize from here.
argument-hint: {company} {pm|swe|mle} <paste full JD text right after>
---

The resume repo is the `resume/` folder of this project (its own git repo). Call its absolute path `{RESUME}` (the project folder is your working directory). Every path in the instructions below is relative to `{RESUME}`, never to the current directory, so output must never land outside it.

- **Read / Edit / Write:** always use the absolute path `{RESUME}/<path>` (for example `{RESUME}/customized/<date>/<Company>_<ROLE>/...`, `{RESUME}/original_tex/...`, `{RESUME}/confirmed_facts.md`).
- **Bash:** start every command with `cd {RESUME} &&` (shell state may reset between calls), then use the relative paths the instructions give.
- Before you finish, run `ls {RESUME}/customized/<date>/<Company>_<ROLE>/` and confirm the `.tex`, `.pdf` and `changes.txt` are there, and that nothing was written to `customized/` at the project root (that folder must not exist).

Then read @resume/.claude/commands/customize.md and follow it exactly, from "Parse the input" to the final summary, with this as `$ARGUMENTS`:

$ARGUMENTS
