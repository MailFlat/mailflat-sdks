"""The registry manifest (`server.json`) must agree with the package it points at.

Why this file exists: publishing to the official MCP Registry needs three things to say the
same word in three places, and nothing at runtime notices when they drift.

  1. `server.json` -> `name`          the registry entry
  2. `README.md`   -> `mcp-name:`     the ownership marker the registry reads from PyPI
  3. `_version.py` -> `__version__`   the version actually shipped

Drift is silent and expensive. If the marker does not match the name, the registry rejects
the publish with "Registry validation failed for package". If the versions do not match, the
registry advertises a version nobody can install. Both failures happen at release time, when
the person publishing has the least patience for a puzzle.

The description limit is checked too: the schema caps it at 100 characters, and a rejected
publish over one long sentence is a bad way to learn that.

Connected to:
  - tests: packages/mcp/server.json, packages/mcp/README.md, src/mailflat_mcp/_version.py

Running it: cd packages/mcp && python -m pytest tests/test_registry_manifest.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "server.json"
README = ROOT / "README.md"
VERSION_FILE = ROOT / "src" / "mailflat_mcp" / "_version.py"

# The registry requires a reverse-DNS name with exactly one slash, and GitHub-authenticated
# publishes must sit under the publisher's own namespace.
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
DESCRIPTION_MAX = 100


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _shipped_version() -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"))
    assert match, "_version.py no longer exposes __version__ in the expected shape"
    return match.group(1)


def test_manifest_name_is_valid_and_owned_by_our_github_namespace():
    name = _manifest()["name"]
    assert NAME_PATTERN.match(name), f"{name!r} is not a valid reverse-DNS registry name"
    assert name.startswith("io.github.MailFlat/"), (
        "GitHub-authenticated publishing only accepts names under the authenticating "
        "namespace; anything else is rejected at publish time"
    )


def test_readme_marker_matches_the_manifest_name():
    """The marker is what proves we own the PyPI package. It has to be the same string."""
    readme = README.read_text(encoding="utf-8")
    match = re.search(r"^mcp-name:\s*(\S+)\s*$", readme, re.M)
    assert match, (
        "README is missing the `mcp-name:` ownership marker; without it the registry "
        "refuses the package with 'Registry validation failed for package'"
    )
    assert match.group(1) == _manifest()["name"]


def test_versions_agree_across_manifest_and_package():
    manifest = _manifest()
    shipped = _shipped_version()
    assert manifest["version"] == shipped, (
        f"server.json says {manifest['version']}, the package ships {shipped}"
    )
    pypi_packages = [p for p in manifest["packages"] if p["registryType"] == "pypi"]
    assert pypi_packages, "the manifest no longer points at a PyPI package"
    assert pypi_packages[0]["identifier"] == "mailflat-mcp"
    assert pypi_packages[0]["version"] == shipped, (
        "the registry would advertise a version that cannot be installed"
    )


def test_description_fits_the_registry_limit():
    description = _manifest()["description"]
    assert 0 < len(description) <= DESCRIPTION_MAX, (
        f"description is {len(description)} characters; the schema caps it at {DESCRIPTION_MAX}"
    )


def test_api_key_is_declared_required_and_secret():
    """A client that does not know the key is secret may print it in a UI or a log."""
    pypi_package = next(p for p in _manifest()["packages"] if p["registryType"] == "pypi")
    key = next(v for v in pypi_package["environmentVariables"] if v["name"] == "MAILFLAT_API_KEY")
    assert key["isRequired"] is True
    assert key["isSecret"] is True
