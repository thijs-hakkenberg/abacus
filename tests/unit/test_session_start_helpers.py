"""RED: pure helpers inside SessionStart -- boundary and rail checks that need
no subprocess, unlike the rest of `test_session_start.py`."""

import json

import pytest


@pytest.fixture
def ss(scripts_path):
    import session_start

    return session_start


# ── _beads_plugin_installed ─────────────────────────────────────────────────

def test_a_settings_file_with_no_beads_plugin_is_not_a_match(ss, harness, monkeypatch):
    monkeypatch.setenv("HOME", str(harness.home))
    settings = harness.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledPlugins": {"other-plugin@some-marketplace": True}}))
    assert ss._beads_plugin_installed() is False


def test_a_beads_plugin_disabled_is_not_a_match(ss, harness, monkeypatch):
    monkeypatch.setenv("HOME", str(harness.home))
    settings = harness.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledPlugins": {"beads@some-marketplace": False}}))
    assert ss._beads_plugin_installed() is False


# ── _within ──────────────────────────────────────────────────────────────────

def test_within_returns_false_when_the_root_cannot_be_resolved(ss, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("nope")

    monkeypatch.setattr(ss.os.path, "expanduser", boom)
    assert ss._within("/some/path", "~/projects") is False


def test_within_returns_false_when_the_boundary_resolves_to_the_filesystem_root(ss):
    """A configured root of "/" itself would make every path on the machine
    "within" it -- refuse the boundary rather than grant that scope."""
    assert ss._within("/some/path", "/") is False


# ── _eligible_for_auto_init ──────────────────────────────────────────────────

def test_not_eligible_when_the_cwd_cannot_be_resolved(ss, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("nope")

    monkeypatch.setattr(ss.os.path, "realpath", boom)
    assert ss._eligible_for_auto_init("/whatever", {}) is False


def test_not_eligible_when_the_cwd_is_not_a_directory(ss, tmp_path):
    a_file = tmp_path / "not-a-dir"
    a_file.write_text("x")
    assert ss._eligible_for_auto_init(str(a_file), {}) is False


def test_not_eligible_when_the_home_or_root_check_itself_errors(ss, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    real_expanduser = ss.os.path.expanduser

    def flaky(p):
        if p == "~":
            raise ValueError("nope")
        return real_expanduser(p)

    monkeypatch.setattr(ss.os.path, "expanduser", flaky)
    assert ss._eligible_for_auto_init(str(repo), {"auto_init": {"roots": []}}) is False