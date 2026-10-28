"""Write Company / Job Title / Job Link / Posted Date rows into the tracker's AUTOMATED tab.

    python -m apply_agent.prepare.sheets --check     # prints the header row
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

from apply_agent.core.config import load_config, tz
from apply_agent.core.models import Job

COLUMNS = ["Company", "Job Title", "Job Link", "Posted Date"]


def worksheet(cfg):
    import gspread  # imported lazily so the rest of the pipeline works without Google set up

    gc = gspread.service_account(filename=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sh = gc.open_by_key(cfg.sheet.id) if cfg.sheet.id else gc.open(cfg.sheet.name)
    return sh.worksheet(cfg.sheet.tab)


def column_map(header: list[str]) -> dict[str, int]:
    norm = {h.strip().lower(): i for i, h in enumerate(header)}
    missing = [c for c in COLUMNS if c.lower() not in norm]
    if missing:
        raise ValueError(f"sheet tab is missing columns {missing}; header is {header}")
    return {c: norm[c.lower()] for c in COLUMNS}


def build_rows(jobs: list[Job], header: list[str], existing_links: set[str], cfg,
               applied: bool = True) -> list[tuple[Job, list[str]]]:
    cols = column_map(header)
    optional = {h.strip().lower(): i for i, h in enumerate(header)
                if h.strip().lower() in ("applied date", "submitted date", "status")}
    width = max([*cols.values(), *optional.values()]) + 1
    out = []
    for j in jobs:
        if j.url in existing_links:
            continue
        row = [""] * width
        row[cols["Company"]] = j.company
        row[cols["Job Title"]] = j.title
        row[cols["Job Link"]] = j.url
        row[cols["Posted Date"]] = j.posted_at.astimezone(tz(cfg)).strftime("%Y-%m-%d") if j.posted_at else ""
        # optional columns, filled only if the tab has them
        for col in ("applied date", "submitted date"):
            if applied and col in optional:
                row[optional[col]] = datetime.now(tz(cfg)).strftime("%Y-%m-%d")
        if applied and "status" in optional:
            row[optional["status"]] = "Applied"
        out.append((j, row))
        existing_links.add(j.url)
    return out


def write(jobs: list[Job], cfg, dry_run: bool = False) -> int:
    ws = worksheet(cfg)
    values = ws.get_all_values()
    header = values[0] if values else []
    link_col = column_map(header)["Job Link"]
    existing = {r[link_col] for r in values[1:] if len(r) > link_col and r[link_col]}
    rows = build_rows(jobs, header, existing, cfg)
    if dry_run or not rows:
        for _, r in rows:
            print("  would add:", r)
        return 0
    first = len(values) + 1
    ws.append_rows([r for _, r in rows], value_input_option="USER_ENTERED")
    for i, (j, _) in enumerate(rows):
        j.sheet_row = first + i
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    ws = worksheet(cfg)
    print("header:", ws.row_values(1))
    print("columns:", column_map(ws.row_values(1)))
