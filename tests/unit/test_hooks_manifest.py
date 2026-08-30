"""RED: the hooks.json wiring itself.

A plugin whose manifest names a script that does not exist fails *silently* —
Claude Code logs it and carries on, so the plugin appears installed and simply
never enforces anything. These tests exist because that failure has no symptom
at runtime; nothing else in the suite would catch a typo'd path.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "hooks" / "hooks.json"

EXPECTED_EVENTS = {
    "SessionStart", "PreToolUse", "PostToolUse", "UserPromptSubmit",
    "Stop", "PreCompact", "SessionEnd",
}


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def entries(manifest):
    """Flatten to (event, matcher, command, timeout) tuples."""
    out = []
    for event, groups in manifest["hooks"].items():
        for group in groups:
            for hook in group.get("hooks", []):
                out.append((event, group.get("matcher"), hook.get("command", ""),
                            hook.get("timeout")))
    return out


def test_the_manifest_is_valid_json(manifest):
    assert isinstance(manifest.get("hooks"), dict)


def test_every_planned_lifecycle_event_is_wired(manifest):
    assert set(manifest["hooks"]) == EXPECTED_EVENTS


def test_every_referenced_script_exists(entries):
    """The failure this whole file exists for: a named script that isn't there."""
    for event, _matcher, command, _timeout in entries:
        for name in re.findall(r"hooks/scripts/([A-Za-z0-9_]+\.py)", command):
            assert (REPO_ROOT / "hooks" / "scripts" / name).is_file(), \
                "%s references missing script %s" % (event, name)


def test_every_command_uses_the_plugin_root_variable(entries):
    """A relative path resolves against the user's cwd, not the plugin dir."""
    for event, _matcher, command, _timeout in entries:
        assert "${CLAUDE_PLUGIN_ROOT}" in command, "%s uses a non-portable path" % event


def test_every_command_falls_back_when_python3_is_absent(entries):
    """Windows installs commonly ship `python` and not `python3`."""
    for event, _matcher, command, _timeout in entries:
        assert "command -v python3" in command and "|| python " in command, \
            "%s would not start without python3" % event


def test_every_hook_has_a_timeout(entries):
    for event, _matcher, _command, timeout in entries:
        assert isinstance(timeout, int) and timeout > 0, "%s has no timeout" % event


def test_timeouts_are_tightest_where_the_user_is_waiting(entries):
    """Budgets are ordered by who is blocked, not by how much work runs.

    UserPromptSubmit is on the user's typing path and gets the least of all.
    PreToolUse blocks every edit, so it must stay well under the hooks that do
    their work in the background — PostToolUse spawns ccusage, and SessionEnd
    talks to a Dolt remote.
    """
    timeouts = {event: timeout for event, _m, _c, timeout in entries}
    assert timeouts["UserPromptSubmit"] < timeouts["PreToolUse"]
    assert timeouts["PreToolUse"] < timeouts["PostToolUse"]
    assert timeouts["PreToolUse"] < timeouts["SessionEnd"]


def test_the_gate_matches_exactly_the_tools_it_claims_to_gate(entries):
    """Drift between the matcher and GATED_TOOLS would mean a tool the script
    thinks it gates never actually reaches it."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "hooks" / "lib"))
    sys.path.insert(0, str(REPO_ROOT / "hooks" / "scripts"))
    import gate_edits

    matcher = next(m for event, m, _c, _t in entries if event == "PreToolUse")
    assert set(matcher.split("|")) == set(gate_edits.GATED_TOOLS)


def test_the_bash_watcher_matches_only_bash(entries):
    matcher = next(m for event, m, _c, _t in entries if event == "PostToolUse")
    assert matcher == "Bash"


def test_precompact_reuses_session_start_with_its_flag(entries):
    command = next(c for event, _m, c, _t in entries if event == "PreCompact")
    assert "session_start.py" in command and "--precompact" in command


def test_the_plugin_manifest_declares_a_name_and_version():
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "abacus"
    assert re.match(r"^\d+\.\d+\.\d+$", plugin["version"]), "version must be SemVer"


def test_the_local_marketplace_points_at_this_repo():
    market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    names = [p.get("name") for p in market.get("plugins", [])]
    assert "abacus" in names
