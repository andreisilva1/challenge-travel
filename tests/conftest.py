"""Fixtures. The costing tests read intermediate.json and never open a PDF.

That is the point of the boundary: costing is a pure function of that file, so its tests
should need nothing else. They run in milliseconds, they do not need Ollama, and they fail
for exactly one reason - the costing logic changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from costing.cost import cost_quotation  # noqa: E402
from domain import ExtractedFacts  # noqa: E402


@pytest.fixture(scope="session")
def facts() -> ExtractedFacts:
    path = ROOT / "intermediate.json"
    if not path.exists():
        pytest.skip("run `python main.py` once to produce intermediate.json")
    return ExtractedFacts.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def result(facts):
    return cost_quotation(facts)


@pytest.fixture(scope="session")
def line(result):
    """Look a costed line up by id, so tests read like the output does."""
    index = {costed.line_id: costed for costed in result.costed_lines}
    return index.__getitem__


@pytest.fixture(scope="session")
def questions(result):
    """Every review item attached to a line, by line id."""
    def of(line_id: str):
        return [item for item in result.needs_review if item.line_id == line_id]
    return of
