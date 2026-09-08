"""Turns on subprocess coverage for hook scripts, but only when asked.

`tests/conftest.py` runs every hook as a real `python3 hooks/scripts/*.py`
child process on purpose (see its module docstring and CLAUDE.md's Testing
section) — that is what catches an import error or a stdout contract
violation a same-process call would miss. The cost is that a plain
`pytest --cov=hooks` never sees that child, because coverage's tracer is
installed in the parent only.

`scripts/coverage.sh` puts this directory on `PYTHONPATH` and sets
`COVERAGE_PROCESS_START`, so every Python child process auto-imports this
module at startup (that is what `sitecustomize` means to the interpreter) and
starts its own coverage measurement, which `coverage combine` merges back
with the parent's afterwards.

A plain `pytest tests/ -q` never has this directory on its `PYTHONPATH`, so
this file is inert for that run and contributors without `coverage` installed
are unaffected — the try/except is there so an interpreter that somehow does
inherit `COVERAGE_PROCESS_START` without `coverage` importable still starts up
rather than failing at site-initialisation. One specific way that import can
fail even with `coverage` installed: the test harness sandboxes `HOME` per
test (tests/conftest.py), and on macOS the user site-packages directory
`coverage` typically lives in is derived from `$HOME` — so `scripts/coverage.sh`
must put that real directory on `PYTHONPATH` explicitly rather than relying on
site-packages discovery to find it under a sandboxed `HOME`.

A second failure mode, found by a teammate probing this file directly: a
`COVERAGE_PROCESS_START` pointing at a config file that does not exist (or is
otherwise unreadable) makes `coverage.process_startup()` raise its own
`ConfigError`, not `ImportError` — the narrower except above did not catch it.
Left uncaught, that exception would propagate out of site initialisation and
crash every one of the ~700 hook subprocesses the suite spawns with a
confusing traceback, which is a worse failure than a wrong coverage number:
this file only instruments measurement, it has no business taking the child
process down with it. Catching broadly here means a misconfigured run stays
quiet at the point of failure — the loud signal belongs in
`scripts/coverage.sh`, which asserts at least one `.coverage.<pid>` data file
was actually produced before it calls `combine`, so a broken config is
reported as an error there instead of silently landing on a too-low number.
"""
import os

try:
    if os.environ.get("COVERAGE_PROCESS_START"):
        import coverage

        coverage.process_startup()
except ImportError:  # only reachable if coverage is not installed
    pass
except Exception:
    # Any other failure to start subprocess coverage (e.g. ConfigError from
    # a missing/unreadable .coveragerc) must not take the hook subprocess
    # down with it -- see the docstring above.
    pass
