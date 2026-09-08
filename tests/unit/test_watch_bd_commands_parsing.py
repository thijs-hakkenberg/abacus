"""RED: pure command-parsing helpers inside the PostToolUse Bash watcher.

`test_watch_bd_commands.py` drives the whole hook as a real subprocess, which is
right for anything with an observable side effect (a snapshot taken, metadata
written). The functions here do neither -- `_git_verb`, `_strip_env_prefix`,
`_flag_value` and `_logical_lines`/`_segments` are pure string transforms -- so a
direct import is used instead, the same way `test_audit.py` tests the audit's
pure detectors separately from `test_audit_cli.py`'s subprocess-driven CLI tests.
"""

import pytest


@pytest.fixture
def wbc(scripts_path):
    import watch_bd_commands

    return watch_bd_commands


# ── git verb detection ───────────────────────────────────────────────────────

def test_git_dash_capital_c_global_is_skipped_to_find_the_real_verb(wbc):
    tokens = ["git", "-C", "subdir", "commit", "-m", "x"]
    assert wbc._git_verb(tokens) == "commit"


def test_a_plain_flag_before_the_verb_is_skipped(wbc):
    tokens = ["git", "--no-pager", "commit", "-m", "x"]
    assert wbc._git_verb(tokens) == "commit"


def test_a_git_invocation_with_no_verb_at_all_returns_none(wbc):
    assert wbc._git_verb(["git", "--version"]) is None


def test_git_push_is_neither_a_move_nor_a_reseed_verb(wbc):
    """`push` does not create a commit locally -- HEAD does not move -- so it must
    fall through both branches rather than being misread as either."""
    events = wbc.parse_events("git push origin main")
    assert events == []


# ── env-prefix stripping ─────────────────────────────────────────────────────

def test_strip_env_prefix_of_an_empty_token_list_returns_empty(wbc):
    assert wbc._strip_env_prefix([]) == []


def test_strip_env_prefix_leaves_a_plain_command_untouched(wbc):
    assert wbc._strip_env_prefix(["bd", "close", "x"]) == ["bd", "close", "x"]


# ── flag value parsing ───────────────────────────────────────────────────────

def test_flag_value_understands_the_equals_spelling(wbc):
    tokens = ["bd-a1b2", "--status=closed"]
    assert wbc._flag_value(tokens, "--status", "-s") == "closed"


# ── segmenting ───────────────────────────────────────────────────────────────

def test_a_leading_separator_contributes_no_empty_segment(wbc):
    assert wbc._segments("&& bd close bd-a1b2") == [["bd", "close", "bd-a1b2"]]


def test_a_trailing_backslash_with_nothing_after_it_is_not_lost(wbc):
    """An open line continuation at the very end of the command (no following
    line at all) must still surface as its own logical line rather than being
    silently dropped."""
    lines = wbc._logical_lines("bd close bd-a1b2 \\")
    assert lines == ["bd close bd-a1b2 \\"]


# ── boundary detection on the parsed tokens ─────────────────────────────────

def test_a_bare_bd_with_nothing_else_is_not_a_boundary(wbc):
    assert wbc.parse_events("bd") == []


def test_an_update_with_neither_claim_nor_a_recognised_status_is_not_a_boundary(wbc):
    assert wbc.parse_events("bd update bd-a1b2 --priority high") == []
