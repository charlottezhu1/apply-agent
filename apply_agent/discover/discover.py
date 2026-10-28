"""Grow companies.yaml from the Simplify feed: every supported-ATS board that posts SWE/PM roles.

    python -m apply_agent.discover.discover            # add new boards (auto: true)
    python -m apply_agent.discover.discover --check    # verify every token returns jobs
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from pathlib import Path

import yaml

from .filters import classify_role
from .sources.ats import SOURCES
from .sources.base import Http
from .sources.simplify import Simplify

COMPANIES = Path(__file__).parent / "companies.yaml"


def load_companies() -> list[dict]:
    if not COMPANIES.exists():
        return []
    return yaml.safe_load(COMPANIES.read_text()) or []


def save_companies(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (r.get("auto", False), r["name"].lower()))
    header = ("# Company job boards the listener fetches. Hand-added rows have no `auto` key;\n"
              "# rows found by discover.py have auto: true. Set enabled: false to skip one.\n")
    COMPANIES.write_text(header + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True, width=200))


def board_key(r: dict) -> tuple:
    return (r["ats"], r.get("tenant") or r.get("token", "").lower())


async def discover() -> int:
    http = Http()
    try:
        listings = await Simplify().list_jobs(http)
    finally:
        await http.aclose()
    rows = load_companies()
    have = {board_key(r) for r in rows}
    counts: Counter = Counter()
    found: dict[tuple, dict] = {}
    for j in listings:
        info = j.extra.get("ats")
        if not info or not classify_role(j.title):
            continue
        if info["ats"] == "workday":
            row = {"name": j.company, "ats": "workday", "host": info["host"], "tenant": info["tenant"],
                   "site": info["site"]}
        elif info.get("token"):
            row = {"name": j.company, "ats": info["ats"], "token": info["token"]}
        else:
            continue
        k = board_key(row)
        counts[k] += 1
        found.setdefault(k, row)
    added = 0
    for k, row in found.items():
        if k not in have:
            rows.append({**row, "auto": True, "simplify_roles": counts[k]})
            added += 1
    save_companies(rows)
    print(f"{len(found)} boards with SWE/PM roles in Simplify; added {added}; total {len(rows)}")
    return added


async def check() -> None:
    http = Http()
    rows = load_companies()

    async def one(r):
        try:
            jobs = await SOURCES[r["ats"]].list_jobs(http, r)
            return r, len(jobs), None
        except Exception as e:
            return r, 0, str(e)[:80]

    results = await asyncio.gather(*(one(r) for r in rows if r.get("enabled", True)))
    await http.aclose()
    bad = 0
    for r, n, err in results:
        if n == 0:
            bad += 1
            print(f"  ✗ {r['name']:<28} {r['ats']:<15} {err or '0 jobs (wrong token?)'}")
    print(f"{len(results) - bad}/{len(results)} boards OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    asyncio.run(check() if a.check else discover())
