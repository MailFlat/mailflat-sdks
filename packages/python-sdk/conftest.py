"""Pytest bootstrap — binds the tests to the LOCAL source, not to an installed package.

Why it exists: this machine had an OLD `pip install mailflat` (0.1.0) and `python -m pytest`
imported that instead of `src/`, so the tests were validating months-old code. New additions
blew up with "cannot import name" or, worse, SILENTLY passed against the old behaviour. The
same trap hit the JS side (B-055: the ai-sdk tests ran against the published `@mailflat/sdk`
until a vitest alias plus tsconfig paths pinned them to local source).

It does two things:
  1. Puts `src/` at the FRONT of sys.path so it wins over site-packages.
  2. VERIFIES that the imported package really came from there — and fails hard at collection
     time if it did not. A silent false green is worse than a red.

Connected to:
  - used by: packages/python-sdk/tests/*
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import mailflat  # noqa: E402 - must be imported AFTER the sys.path tweak above

_loaded = Path(mailflat.__file__).resolve()
if _SRC not in _loaded.parents:
    raise RuntimeError(
        f"Tests are importing an installed 'mailflat' from {_loaded}, not the local source "
        f"at {_SRC}. Uninstall it (pip uninstall mailflat) or run with PYTHONPATH=src."
    )
