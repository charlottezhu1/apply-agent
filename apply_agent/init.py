"""Create applicant/ from templates/applicant/ (never overwrites a file that already exists).

    python -m apply_agent.init
"""
from __future__ import annotations

import shutil
import sys

from apply_agent.core.config import APPLICANT, ROOT

TEMPLATES = ROOT / "templates" / "applicant"


def init() -> list[str]:
    created = []
    APPLICANT.mkdir(parents=True, exist_ok=True)
    for src in sorted(TEMPLATES.iterdir()):
        dest = APPLICANT / src.name
        if not dest.exists():
            shutil.copyfile(src, dest)
            created.append(src.name)
    return created


def main() -> int:
    created = init()
    print(f"applicant folder: {APPLICANT}")
    for c in created:
        print(f"  created {c}")
    if not created:
        print("  nothing to create: all files already exist")
    else:
        print("\nNext: fill in applicant/profile.yaml (replace every CHANGE_ME), then run: python -m apply_agent.sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
