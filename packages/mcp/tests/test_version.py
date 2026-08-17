"""One version source only — prevents a repeat of B-052 (published 0.2.1, package said "0.1.0").

Same rationale and method as python-sdk/tests/test_version.py: with hatchling's `dynamic` plus
`[tool.hatch.version].path`, metadata is read from `__init__.py` at build time, so "there is no
second version source" is itself the proof that the version on PyPI is correct.

Connected to:
  - tests: pyproject.toml ([project].dynamic, [tool.hatch.version]) ↔ src/mailflat_mcp/__init__.py

Running it: cd packages/mcp && python -m pytest tests/test_version.py
"""
import re
from pathlib import Path

import pytest

import mailflat_mcp

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "mailflat_mcp" / "_version.py"


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib", reason="tomllib requires Python 3.11+")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_has_no_second_version():
    """🔒 pyproject must not hardcode a version."""
    data = _pyproject()
    assert "version" not in data["project"], (
        "pyproject.toml hardcodes a version again; drifting from __version__ is a matter of time"
    )
    assert "version" in data["project"].get("dynamic", [])


def test_build_reads_the_version_from_the_dunder():
    data = _pyproject()
    assert data["tool"]["hatch"]["version"]["path"] == "src/mailflat_mcp/_version.py"


def test_dunder_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+([abrc.\-+].*)?", mailflat_mcp.__version__), mailflat_mcp.__version__


def test_dunder_is_a_plain_literal_in_the_source():
    """Hatchling reads the file with a regex, so a computed version desyncs the build."""
    match = re.search(r'^__version__ = "([^"]+)"$', VERSION_FILE.read_text(encoding="utf-8"), re.M)
    assert match, "no plain `__version__ = \"X.Y.Z\"` line found in _version.py"
    assert match.group(1) == mailflat_mcp.__version__
