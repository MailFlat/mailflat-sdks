"""One version source only — prevents a repeat of B-052 (published 0.3.1, package said "0.3.0").

Why a structural test: when hatchling sees `dynamic = ["version"]` plus
`[tool.hatch.version].path`, it reads the wheel/sdist metadata FROM THAT FILE AT BUILD TIME.
So "pyproject declares no second version and the path points at `__init__.py`" is itself the
proof that the version on PyPI will match `__version__`. Comparing installed metadata
(`importlib.metadata`) would be WRONG here: an editable install freezes its metadata at
install time, so it goes red about the freshness of the venv rather than about the code.

Connected to:
  - tests: pyproject.toml ([project].dynamic, [tool.hatch.version]) ↔ src/mailflat/__init__.py

Running it: cd packages/python-sdk && python -m pytest tests/test_version.py
"""
import re
from pathlib import Path

import pytest

import mailflat

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src" / "mailflat" / "__init__.py"


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib", reason="tomllib requires Python 3.11+")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_has_no_second_version():
    """🔒 pyproject must not hardcode a version — two sources drift apart eventually."""
    data = _pyproject()
    assert "version" not in data["project"], (
        "pyproject.toml hardcodes a version again; drifting from __version__ is a matter of time"
    )
    assert "version" in data["project"].get("dynamic", []), (
        "[project].dynamic must contain 'version', otherwise hatchling never reads __init__.py"
    )


def test_build_reads_the_version_from_the_dunder():
    """🔒 The build must read its version from the very file where `__version__` lives."""
    data = _pyproject()
    assert data["tool"]["hatch"]["version"]["path"] == "src/mailflat/__init__.py"


def test_dunder_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+([abrc.\-+].*)?", mailflat.__version__), mailflat.__version__


def test_dunder_is_a_plain_literal_in_the_source():
    """The version the module reports must be the literal string in the file.

    Hatchling reads the file with a regex WITHOUT executing it, so a computed `__version__`
    would make build and runtime disagree all over again.
    """
    match = re.search(r'^__version__ = "([^"]+)"$', INIT.read_text(encoding="utf-8"), re.M)
    assert match, "no plain `__version__ = \"X.Y.Z\"` line found in __init__.py"
    assert match.group(1) == mailflat.__version__
