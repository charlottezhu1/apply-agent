"""The macOS pop-up at the end of each run. No dependencies: osascript ships with macOS."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def popup(message: str, open_path: Path | None = None, button: str = "Open summary") -> None:
    """Show a dialog; if the user clicks `button`, open `open_path`. Never raises."""
    if sys.platform != "darwin":
        print(f"[notify] {message}")
        return
    buttons = f'{{"Later", "{button}"}}' if open_path else '{"OK"}'
    default = f'"{button}"' if open_path else '"OK"'
    script = (f'display dialog "{_esc(message)}" with title "apply_agent" '
              f"buttons {buttons} default button {default} giving up after 43200")
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=43300).stdout
        if open_path and f"button returned:{button}" in out:
            subprocess.run(["open", "-t", str(open_path)], check=False)
    except Exception as e:  # a missing pop-up must never break the run
        print(f"[notify] failed: {e}: {message}")
