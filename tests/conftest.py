from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES))


@pytest.fixture(scope="session")
def manual_path(tmp_path_factory) -> Path:
    from make_manual import build

    return build(tmp_path_factory.mktemp("manual") / "lab03_manual.docx")


@pytest.fixture
def sandbox():
    from labsagent.sandbox.local import LocalSandbox

    with LocalSandbox() as sb:
        yield sb
