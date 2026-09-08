#!/bin/sh
# Measures hooks/ coverage, including hooks/scripts/*.py, which the suite
# always runs as real subprocesses (see tests/conftest.py's module docstring
# and CLAUDE.md's Testing section for why). A plain
# `pytest --cov=hooks` under-reports badly: coverage's tracer lives in the
# parent process, so a child spawned by subprocess.run() is invisible to it,
# and scripts that are never imported in-process (e.g. watch_bd_commands.py)
# show up as 0% even though the suite exercises them constantly.
#
# This wires up coverage's own subprocess support instead: COVERAGE_PROCESS_START
# plus .covboot/sitecustomize.py on PYTHONPATH make every `python3
# hooks/scripts/*.py` child start its own coverage measurement the moment the
# interpreter boots. `coverage combine` then merges every child's data file
# with the parent pytest process's, into one report that covers the whole
# hooks/ tree honestly.
set -eu
cd "$(dirname "$0")/.."

rm -f .coverage .coverage.[0-9A-Za-z]*
export COVERAGE_PROCESS_START="$PWD/.coveragerc"
export COVERAGE_FILE="$PWD/.coverage"
export ABACUS_COVERAGE_SOURCE="$PWD/hooks"

# The suite sandboxes $HOME per test (tests/conftest.py), and on macOS the
# user site-packages directory is derived from $HOME — so under a sandboxed
# HOME, a `coverage` installed only in the real user site-packages is not
# importable by the hook subprocess. Put the real location on PYTHONPATH
# explicitly rather than relying on site-packages discovery to find it.
_user_site="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
export PYTHONPATH="$PWD/.covboot${_user_site:+:$_user_site}${PYTHONPATH:+:$PYTHONPATH}"

python3 -m coverage run -m pytest tests/ -q

# If .coveragerc went missing or COVERAGE_PROCESS_START couldn't be read,
# every hook subprocess's sitecustomize.py fails open and quietly measures
# nothing (see .covboot/sitecustomize.py's docstring) -- the suite still
# passes and `combine` would have nothing to merge, silently handing back
# the same too-low parent-only number a naive `pytest --cov=hooks` gives.
# Fail loudly here instead of letting that happen a second time.
if ! ls .coverage.[0-9A-Za-z]* >/dev/null 2>&1; then
    echo "ERROR: no per-process coverage data files (.coverage.<host>.<pid>.<rand>) were produced." >&2
    echo "Subprocess coverage did not engage -- check COVERAGE_PROCESS_START, .coveragerc and .covboot/sitecustomize.py." >&2
    exit 1
fi

python3 -m coverage combine
python3 -m coverage report -m "$@"
