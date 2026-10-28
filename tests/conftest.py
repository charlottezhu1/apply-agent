import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read the real applicant/ folder: point the app at a throwaway copy of a made-up applicant.
# This must happen before apply_agent is imported, because the paths are fixed at import time.
_APPLICANT = Path(tempfile.mkdtemp(prefix="applicant-test-"))
shutil.copytree(Path(__file__).parent / "fixtures" / "applicant", _APPLICANT, dirs_exist_ok=True)
os.environ["APPLY_AGENT_APPLICANT"] = str(_APPLICANT)


@pytest.fixture
def cfg():
    return SimpleNamespace(
        jobs_per_day=3, timezone="America/Los_Angeles",
        search=SimpleNamespace(roles=["SWE", "PM"], include_internships=False, max_years_required=2,
                               location_mode="bay_area_or_remote", max_age_days=30),
        rank=SimpleNamespace(match_weight=0.7, half_life_days=14, max_per_company=2),
    )
