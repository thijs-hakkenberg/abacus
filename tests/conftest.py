"""Shared test harness for abacus.

Every hook script is exercised as a *real subprocess* with a JSON payload on
stdin, because that is exactly how Claude Code invokes it — an in-process call
would not catch an import error, a stdout contract violation, or a non-zero
exit that only manifests under the real entrypoint.

Two isolation guarantees hold for every test:

- ``HOME`` points at a per-test tmp dir, so no test can read or write the
  developer's real ``~/.claude/`` or ``~/.beads/``.
- ``PATH`` is prefixed with a stub bin dir, so ``bd`` and ``npx`` are fakes
  that record their argv to ``calls.log``. Nothing here shells out to the real
  ccusage (no network, no npx download) or mutates a real beads database.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "hooks" / "scripts"
LIB = REPO_ROOT / "hooks" / "lib"

# pytest-bdd resolves step definitions from the namespace of the module that
# calls `scenarios()`, so tests/features/test_features.py star-imports them by
# bare name. Their directory is put on sys.path here rather than in a
# tests/features/conftest.py, because a second conftest module would shadow the
# name `conftest` and break the `from conftest import ...` the unit tests use.
sys.path.insert(0, str(Path(__file__).resolve().parent / "features" / "steps"))


# ── stub executables ────────────────────────────────────────────────────────
# Each stub appends its argv to $STUB_CALLS_LOG then prints whatever the test
# planted in a small control file. Keeping them as tiny shell scripts (rather
# than Python) keeps stub startup off the measured hot path.

_BD_STUB = r"""#!/bin/sh
printf '%s\n' "bd $*" >> "$STUB_CALLS_LOG"
_sub="$1"
# Per-subcommand canned output: $STUB_DIR/bd.<subcommand>.out, else bd.out
if [ -f "$STUB_DIR/bd.$_sub.rc" ]; then _rc=$(cat "$STUB_DIR/bd.$_sub.rc"); else _rc=0; fi
if [ -f "$STUB_DIR/bd.$_sub.out" ]; then
    cat "$STUB_DIR/bd.$_sub.out"
elif [ -f "$STUB_DIR/bd.out" ]; then
    cat "$STUB_DIR/bd.out"
fi
exit "$_rc"
"""

_NPX_STUB = r"""#!/bin/sh
printf '%s\n' "npx $*" >> "$STUB_CALLS_LOG"
if [ -f "$STUB_DIR/npx.sleep" ]; then sleep "$(cat "$STUB_DIR/npx.sleep")"; fi
if [ -f "$STUB_DIR/npx.rc" ]; then _rc=$(cat "$STUB_DIR/npx.rc"); else _rc=0; fi
if [ -f "$STUB_DIR/npx.out" ]; then cat "$STUB_DIR/npx.out"; fi
exit "$_rc"
"""


class Harness:
    """Handle a test uses to plant stub behaviour and run hook scripts."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.home = tmp_path / "home"
        self.stub_dir = tmp_path / "stubbin"
        self.project = tmp_path / "project"
        self.state_dir = self.home / ".claude" / "abacus"
        for d in (self.home, self.stub_dir, self.project, self.state_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.calls_log = tmp_path / "calls.log"
        self.calls_log.write_text("")

        for name, body in (("bd", _BD_STUB), ("npx", _NPX_STUB)):
            p = self.stub_dir / name
            p.write_text(body)
            p.chmod(0o755)

    # -- planting stub behaviour ------------------------------------------
    def set_bd(self, subcommand, stdout="", rc=0):
        """Plant canned stdout/exit-code for `bd <subcommand>`."""
        if subcommand is None:
            (self.stub_dir / "bd.out").write_text(stdout)
            return
        (self.stub_dir / f"bd.{subcommand}.out").write_text(stdout)
        (self.stub_dir / f"bd.{subcommand}.rc").write_text(str(rc))

    def set_bd_json(self, subcommand, obj, rc=0):
        self.set_bd(subcommand, json.dumps(obj), rc)

    def remove_bd(self):
        """Simulate `bd` absent from PATH."""
        (self.stub_dir / "bd").unlink()

    def remove_npx(self):
        """Simulate `npx` absent from PATH — no cost source at all."""
        (self.stub_dir / "npx").unlink()

    def set_ccusage_session(self, session_id, cost, tokens, **extra):
        """Plant a ccusage `session --json` payload the npx stub will echo."""
        row = {
            "sessionId": session_id,
            "projectPath": str(self.project),
            "totalCost": cost,
            "totalTokens": tokens,
            "inputTokens": extra.get("input_tokens", 0),
            "outputTokens": extra.get("output_tokens", 0),
            "cacheReadTokens": extra.get("cache_read_tokens", 0),
            "cacheCreationTokens": extra.get("cache_creation_tokens", 0),
            "modelsUsed": extra.get("models", ["claude-fable-5"]),
        }
        payload = {"sessions": [row], "totals": {"totalCost": cost, "totalTokens": tokens}}
        (self.stub_dir / "npx.out").write_text(json.dumps(payload))

    def set_ccusage_raw(self, text, rc=0):
        (self.stub_dir / "npx.out").write_text(text)
        (self.stub_dir / "npx.rc").write_text(str(rc))

    def set_ccusage_hang(self, seconds):
        (self.stub_dir / "npx.sleep").write_text(str(seconds))

    def make_beads_project(self):
        """Give the project dir a `.beads/` so the gate treats it as tracked."""
        (self.project / ".beads").mkdir(exist_ok=True)
        return self.project

    def make_git_project(self, path=None):
        """A git repository with no beads workspace — auto-init's only target.

        Only the `.git` marker is created, not a real repository: nothing under
        test runs git, and `bd init --stealth` (which does) is a stub here.
        """
        root = Path(path) if path else self.project
        root.mkdir(parents=True, exist_ok=True)
        (root / ".git" / "info").mkdir(parents=True, exist_ok=True)
        return root

    def write_config(self, cfg):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "config.json").write_text(json.dumps(cfg))

    def read_state(self, session_id):
        p = self.state_dir / f"session-{session_id}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def write_state(self, session_id, state):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / f"session-{session_id}.json").write_text(json.dumps(state))

    # -- observing stub calls ---------------------------------------------
    def calls(self):
        return [ln for ln in self.calls_log.read_text().splitlines() if ln.strip()]

    def bd_calls(self):
        return [c for c in self.calls() if c.startswith("bd ")]

    def npx_calls(self):
        return [c for c in self.calls() if c.startswith("npx ")]

    # -- running the real hook scripts ------------------------------------
    def env(self, **overrides):
        env = dict(os.environ)
        env["PATH"] = f"{self.stub_dir}{os.pathsep}{env.get('PATH', '')}"
        env["HOME"] = str(self.home)
        env["STUB_DIR"] = str(self.stub_dir)
        env["STUB_CALLS_LOG"] = str(self.calls_log)
        env["ABACUS_STATE_DIR"] = str(self.state_dir)
        # Never let a developer's real kill-switch leak into a test run.
        env.pop("ABACUS_DISABLE", None)
        env.pop("BEADS_DIR", None)
        for k, v in overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = str(v)
        return env

    def run_hook(self, script, payload, cwd=None, extra_args=(), **env_overrides):
        """Run a hook script exactly as Claude Code does: JSON on stdin."""
        path = SCRIPTS / script
        proc = subprocess.run(
            [sys.executable, str(path), *extra_args],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(cwd or self.project),
            env=self.env(**env_overrides),
            timeout=60,
        )
        return HookResult(proc)

    def run_hook_raw(self, script, stdin_text, cwd=None, extra_args=(), **env_overrides):
        """Run a hook script with arbitrary (possibly non-JSON) stdin."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script), *extra_args],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(cwd or self.project),
            env=self.env(**env_overrides),
            timeout=60,
        )
        return HookResult(proc)


class HookResult:
    def __init__(self, proc):
        self.rc = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr

    @property
    def json(self):
        """Parsed stdout, or None when the hook printed nothing."""
        if not self.stdout.strip():
            return None
        return json.loads(self.stdout)

    @property
    def permission_decision(self):
        data = self.json or {}
        return (data.get("hookSpecificOutput") or {}).get("permissionDecision")

    @property
    def reason(self):
        data = self.json or {}
        return (data.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")

    def __repr__(self):
        return f"<HookResult rc={self.rc} stdout={self.stdout[:200]!r} stderr={self.stderr[:200]!r}>"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


@pytest.fixture
def lib_path(monkeypatch):
    """Put hooks/lib on sys.path for direct unit tests of the library modules."""
    monkeypatch.syspath_prepend(str(LIB))
    return LIB


def pre_tool_payload(session_id="sess-1", tool="Edit", cwd=None, **extra):
    p = {
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": extra.pop("tool_input", {"file_path": "/tmp/x.py", "old_string": "a", "new_string": "b"}),
    }
    if cwd:
        p["cwd"] = str(cwd)
    p.update(extra)
    return p


def post_bash_payload(command, session_id="sess-1", cwd=None, **extra):
    p = {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": extra.pop("tool_response", {"stdout": "", "stderr": "", "interrupted": False}),
    }
    if cwd:
        p["cwd"] = str(cwd)
    p.update(extra)
    return p


def session_payload(session_id="sess-1", source="startup", event="SessionStart", cwd=None, **extra):
    p = {"session_id": session_id, "hook_event_name": event, "source": source}
    if cwd:
        p["cwd"] = str(cwd)
    p.update(extra)
    return p
