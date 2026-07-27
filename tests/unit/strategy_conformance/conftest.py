"""Ensure worker children can import strategy_conformance test probes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_TESTS_UNIT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _probe_pythonpath() -> None:
    current = os.environ.get("PYTHONPATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    root = str(_TESTS_UNIT)
    if root not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])
