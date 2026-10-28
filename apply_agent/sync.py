"""Push the applicant's files to where the other tools read them.

    python -m apply_agent.sync

  * applicant/profile.yaml  → chrome_extension/generated/profile.json (the extension's autofill values)
  * applicant/facts.md      → resume/confirmed_facts.md (the copy the tailoring sessions read)

The daily run and every tailoring run do this themselves; run it by hand after editing applicant/.
"""
from __future__ import annotations

import sys

from apply_agent.core import resume_text
from apply_agent.core.profile import EXT_PROFILE, ProfileError, load_profile, write_extension_profile


def sync() -> list[str]:
    """Returns what was refreshed."""
    done = []
    write_extension_profile(load_profile())
    done.append(str(EXT_PROFILE))
    if resume_text.sync_facts_mirror():
        done.append(str(resume_text.FACTS_MIRROR))
    return done


def main() -> int:
    try:
        done = sync()
    except ProfileError as e:
        print(f"✗ {e}")
        return 1
    for d in done:
        print(f"✓ {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
