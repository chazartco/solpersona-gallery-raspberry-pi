import json
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
REPO = AGENT.parent
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(REPO / "dev"))

VECTORS = REPO / "docs" / "solpersona" / "test-vectors.json"


@pytest.fixture(scope="session")
def vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))
