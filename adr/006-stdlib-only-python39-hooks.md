# ADR 006: Hooks are pure-stdlib Python 3.9 with no install step

## Status

Accepted

## Date

2026-08-05

## Context

Hooks run on every session start, every prompt, every Bash call and every edit.
Their startup cost and their reliability are both user-visible, and the gate's
in particular sits directly in front of the user's work.

The environment on this machine, measured:

- **`python3` is 3.9.6**, the macOS system interpreter, with no venv. `pytest`
  8.4.2, `pytest-bdd` 8.1.0 and `pyyaml` 6.0.3 are importable *for tests*, but
  the hooks themselves must not assume anything beyond stdlib.
- **`bats-core` is installed** — a real option for testing shell-invoked hooks.
- The author has shipped a plugin that needed non-stdlib dependencies for an MCP
  server, and spent two ADRs on the consequences: one on making the server
  self-bootstrap a venv, and one on the fact that hook subprocesses must then be
  launched with *that* venv's interpreter. The second existed only because a hook
  that shelled out to Python got the wrong interpreter and could not import what it
  needed.

Python 3.9 is genuinely old enough to constrain how the code is written. Not
available: `match` statements, `X | Y` union syntax at runtime, `tomllib`,
`dict |` merging, `str.removeprefix` in some contexts, and several typing
conveniences.

## Decision

Every file under `hooks/` imports only the standard library, and the plugin has no
install step.

- **Stdlib only, targeting 3.9.6.** `json`, `os`, `re`, `shutil`, `subprocess`,
  `sys`, `tempfile`, `time`, `shlex`, `datetime`. No third-party import anywhere in
  `hooks/`. No `requirements.txt` for the runtime, no venv, no bootstrap.
- **The portable interpreter idiom in `hooks.json`**, which is the surviving lesson
  from that interpreter bug:
  `command -v python3 >/dev/null 2>&1 && python3 "$X" || python "$X"`.
  A bare `python3` is absent on some Windows installs while `python` is absent on
  many Linux ones, and a hook that cannot start is indistinguishable from a plugin
  that does nothing.
- **`hooks/lib/` on `sys.path` explicitly**, from each script, relative to its own
  `__file__`. No package, no `__init__.py`, no reliance on cwd.
- **Library modules are prefixed where a collision is plausible.**
  `abacus_config.py` rather than `config.py` and `abacus_time.py` rather than `time.py`:
  a plugin host may put several plugins' `lib` directories on the path, and a
  module named `config` there is a name waiting to shadow something.
- **Tests may use third-party packages** — pytest and pytest-bdd — because they
  never run inside a hook. The suite drives hooks as real subprocesses with
  fixture JSON on stdin, `bd`/`npx` stubbed on `PATH`, and a sandboxed
  `HOME`/`TMPDIR`.
- **Python, not bats, for the tests**, despite bats being installed. The hooks are
  Python; the fixtures are JSON payloads; the assertions are about parsed JSON
  output and recorded argv. Doing that in bats means shelling out to `python -c`
  or `jq` for every assertion, which is a worse harness for the same work. bats
  would only have been the better choice if the hooks were shell scripts.

## Consequences

### Positive
- Zero install, zero bootstrap, zero interpreter-resolution problem — the two
  things that previously needed dedicated ADRs simply do not exist here. Cloning
  the repo and enabling the plugin is the whole setup.
- Hook startup is just interpreter startup, measured at ~0.10s for python3.9 on
  this machine. On the gate's hot path that is most of the total, with `bd list`
  the rest.
- No dependency can break a session by being absent, outdated, or mid-upgrade.
  There is nothing to be absent.
- Portable to any machine with any Python 3 without provisioning.

### Negative
- Python 3.9 is restrictive to write against, and the restriction is easy to
  violate accidentally by anyone used to 3.10+. `match`, `X | Y` at runtime,
  `tomllib` and `dict |` are all unavailable. Mitigated only by the tests running
  on 3.9.6 itself, which catches a syntax-level violation immediately and a
  runtime-typing one on first execution.
- No `pydantic`, no `requests`, no `yaml` in the runtime. Config is JSON rather
  than YAML specifically because `json` is stdlib and `yaml` is not — this is why
  `~/.claude/abacus/config.json` is JSON while `spec.manifest.yaml`
  (read only by tests) is YAML.
- Validation is hand-rolled. Every config value has a safe default and a
  malformed config falls back to defaults silently (`abacus_config.load_config`
  swallows everything), which is right for a hook but means a typo in the config
  file is not reported to the user.
- If an MCP server is ever added (adr/004 says not in v1), it will need the
  bootstrap machinery this decision avoided, and the interpreter-resolution lesson
  above should be applied rather than re-derived.

### Neutral
- `pyyaml` is used by `tests/unit/test_spec_conformance.py` to parse
  `spec.manifest.yaml`. That is a test-time dependency and is already installed.
- Every script ends with `hook_io.guard(main)`, which catches everything and
  exits 0 — so even a `SyntaxError`-free-but-broken module degrades rather than
  producing a traceback in the user's session. (The gate's deny is data on
  stdout, not an exit code, so this holds for it too — adr/002.)

## Alternatives Considered

### Alternative 1: A bootstrap venv

Rejected. It is the correct answer when you genuinely need non-stdlib packages,
and the wrong answer when you do not: it adds a first-run install, an
interpreter-resolution problem for every subprocess (an ADR the author wrote
elsewhere exists because of exactly that bug), and a failure mode where the venv is
stale or partially built. Nothing this plugin does needs a dependency.

### Alternative 2: Shell scripts, tested with bats

Rejected. The logic includes JSON parsing on stdin, JSON emission on stdout,
snapshot arithmetic, ISO-8601 duration maths, and a shell-command tokeniser
(`shlex` with `punctuation_chars=True`). All of that in shell means `jq` on every
path — adding a dependency to avoid a dependency — and the tokeniser in
particular would be substantially harder to get right and to test.

### Alternative 3: Target 3.11+ and require a newer interpreter

Rejected. The available interpreter on this machine is 3.9.6, and requiring the
user to provision a newer one to use a plugin is precisely the install step this
decision is avoiding. The 3.9 constraint costs some syntactic convenience and
buys running everywhere unmodified.
