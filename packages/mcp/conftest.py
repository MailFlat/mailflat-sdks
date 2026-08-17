"""Pytest bootstrap — binds the tests to LOCAL source (both mailflat_mcp and its sibling SDK).

Why it exists: this machine had an OLD `pip install mailflat` (0.1.0), and tests run without
PYTHONPATH imported THAT — so the new SDK methods (mark_read, burn, direction) looked absent.
The JS version of the same trap is recorded as B-055.

Binding the sibling source also matters because `mailflat_mcp` depends on `mailflat>=0.4.1`:
testing both together before publishing is the only way to catch a mismatch early.

Connected to:
  - used by: packages/mcp/tests/*
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PATHS = [_HERE / "src", _HERE.parent / "python-sdk" / "src", _HERE / "tests"]
for _p in _PATHS:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import mailflat  # noqa: E402
import mailflat_mcp  # noqa: E402

for _mod, _root in ((mailflat, _PATHS[1]), (mailflat_mcp, _PATHS[0])):
    _loaded = Path(_mod.__file__).resolve()
    if _root not in _loaded.parents:
        raise RuntimeError(
            f"Tests are importing an installed '{_mod.__name__}' from {_loaded}, not the local "
            f"source at {_root}. Uninstall it or run with PYTHONPATH set."
        )
