"""Code-only checks on a customized .tex. Never blocks; returns warnings for SUMMARY.md."""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

SCAFFOLD = re.compile(r"\\(usepackage|newcommand|renewcommand|NewDocumentCommand|addtolength|setlength|"
                      r"titleformat|titlespacing|documentclass|definecolor|pagestyle|input)\b")
_BOLD_NUM = re.compile(r"\\textbf\{([^{}]*\d[^{}]*)\}")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")  # the value only: "0.868.", "18%" and "18" all compare as written digits
_TERM = re.compile(r"\b(?:[A-Z][a-z]+[A-Z]\w*|[A-Z]{2,}[a-z]*s?|\w+(?:\+\+|#|\.js|\.ai|\.io)|[A-Za-z]+/[A-Za-z]+)\b"
                   r"|\b\w+\+\+")


def _plain(s: str) -> str:
    s = re.sub(r"\\(textbf|textit|emph|href\{[^}]*\})\{", "", s)
    # "}{" separates LaTeX arguments; turn braces into spaces so "Intern}{ADVANCE" doesn't become one word
    return s.replace("\\%", "%").replace("\\&", "&").replace("{", " ").replace("}", " ")


def changed_lines(base_tex: str, new_tex: str) -> list[str]:
    diff = difflib.unified_diff(base_tex.splitlines(), new_tex.splitlines(), lineterm="", n=0)
    return [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]


def scaffolding_changed(base_tex: str, new_tex: str) -> list[str]:
    diff = difflib.unified_diff(base_tex.splitlines(), new_tex.splitlines(), lineterm="", n=0)
    return [l for l in diff if l[:1] in "+-" and l[:3] not in ("+++", "---") and SCAFFOLD.search(l)]


def warnings(base_tex: str, new_tex: str, known_text: str, allowlist: list[str]) -> list[str]:
    out = []
    # 1. every number bolded in the base must still appear, unchanged, somewhere in the new resume
    new_nums = set(_NUM.findall(_plain(new_tex)))
    for bold in _BOLD_NUM.findall(base_tex):
        for num in _NUM.findall(_plain(bold)):
            if num not in new_nums:
                out.append(f"number changed or removed: {num} (was: {_plain(bold).strip()})")
    # 2. tech-looking terms added by the edit that appear nowhere in the base or confirmed facts
    known = (base_tex + "\n" + known_text).lower()
    allow = {a.lower() for a in allowlist}
    seen = set()
    for line in changed_lines(base_tex, new_tex):
        for t in _TERM.findall(_plain(line)):
            tl = t.lower()
            parts = [p for p in re.split(r"/", tl) if p]
            if tl in allow or tl in seen or all(p in known or p in allow for p in parts):
                continue
            seen.add(tl)
            out.append(f"new term: {t}")
    return out


def pdf_pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0


def rendered_lines(pdf: Path) -> int:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if l.strip())
