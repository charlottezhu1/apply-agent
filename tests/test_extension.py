"""The extension's scripts run for real (in Node, with stubbed browser APIs) against a generated profile.json."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from apply_agent.core import profile as P

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def test_extension_loads_and_uses_profile_json(tmp_path):
    dest = P.write_extension_profile(P.load_profile(), tmp_path / "profile.json")
    run = subprocess.run(["node", str(Path(__file__).parent / "ext_smoke.js"), str(dest)], capture_output=True,
                         text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["initialKeys"] == 0                      # config.js carries no personal values
    assert out["fillOnEmptyPage"] == 0 and out["loadedKeys"] == len(P.load_profile().form_profile())
    assert out["email"] == "sam.tester@example.com"
    assert out["website"] == "https://github.com/sam-tester"          # no website → GitHub
    assert out["location"] == "Exampleton, California"
    assert out["degree"] == ["Bachelor's", "BS"]
    assert out["eduEndYear"] == "2028"
    assert (out["legalFirst"], out["everydayFirst"]) == ("Samuel", "Sam")
    assert out["missingFill"] == 0 and out["missingFlag"] is True      # no profile.json → nothing filled, flagged
