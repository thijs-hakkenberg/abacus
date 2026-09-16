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
That does *not* crash the child. Verified empirically by the same teammate:
`site.execsitecustomize()` already wraps this whole module's execution in
its own broad `except Exception`, so an uncaught `ConfigError` here still
prints one quiet, lowercase `Error in sitecustomize; ...` line to stderr —
and the child runs to completion and exits 0, having written **no data
file** for that process. The failure mode is silent under-reporting, not a
crash: no traceback, no non-zero exit, nothing the suite's
`"Traceback" not in res.stderr` assertions would ever catch, just one
process's worth of coverage quietly missing from the eventual `combine`.
Catching the `ConfigError` here does not prevent a crash that was never
going to happen; it only removes that one stderr line, which is currently
the *only* local symptom of a broken config, in exchange for quietness at
exactly the layer this file lives in. The loud signal on purpose lives one
layer up, in `scripts/coverage.sh`, which asserts at least one
`.coverage.<pid>` data file was actually produced before it calls
`combine` — that is what turns this silent under-reporting into a reported
error instead of a too-low number nobody notices, whether or not this
except is here.
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
    # a missing/unreadable .coveragerc). site.execsitecustomize() would catch
    # this anyway and let the child continue -- this except only silences the
    # one stderr line that failure would otherwise print. See the docstring.
    pass
