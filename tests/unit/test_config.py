"""RED: config defaults, user overrides, and the kill switch."""

import json

import pytest


@pytest.fixture
def conf(lib_path, harness, monkeypatch):
    monkeypatch.setenv("ABACUS_STATE_DIR", str(harness.state_dir))
    monkeypatch.delenv("ABACUS_DISABLE", raising=False)
    import abacus_config

    return abacus_config


def test_defaults_are_returned_when_no_config_file_exists(conf):
    cfg = conf.load_config()
    assert cfg["gate"]["non_beads_project"] == "warn"
    assert cfg["ccusage_offline"] is False
    assert cfg["prime"]["enabled"] is True


def test_ccusage_version_is_pinned_not_latest(conf):
    version = conf.load_config()["ccusage_version"]
    assert version.startswith("ccusage@")
    assert "latest" not in version, "pricing tables must not float (adr/003)"


def test_user_config_overrides_defaults(conf, harness):
    harness.write_config({"ccusage_timeout_s": 42, "gate": {"non_beads_project": "block"}})
    cfg = conf.load_config()
    assert cfg["ccusage_timeout_s"] == 42
    assert cfg["gate"]["non_beads_project"] == "block"


def test_nested_user_config_does_not_drop_sibling_defaults(conf, harness):
    """A partial `gate` block must not erase the other gate defaults."""
    harness.write_config({"gate": {"non_beads_project": "off"}})
    cfg = conf.load_config()
    assert cfg["gate"]["non_beads_project"] == "off"
    assert "enabled" in cfg["gate"], "sibling gate defaults must survive a partial override"


def test_malformed_config_falls_back_to_defaults(conf, harness):
    (harness.state_dir / "config.json").write_text("{{{ not json")
    cfg = conf.load_config()
    assert cfg["gate"]["non_beads_project"] == "warn"


def test_env_kill_switch_disables_the_plugin(conf, monkeypatch):
    assert conf.is_disabled() is False
    monkeypatch.setenv("ABACUS_DISABLE", "1")
    assert conf.is_disabled() is True


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("yes", True),
                                            ("0", False), ("", False), ("false", False)])
def test_kill_switch_accepts_common_truthy_spellings(conf, monkeypatch, value, expected):
    monkeypatch.setenv("ABACUS_DISABLE", value)
    assert conf.is_disabled() is expected


def test_marker_file_disables_the_plugin(conf, harness):
    (harness.state_dir / "disabled").write_text("")
    assert conf.is_disabled() is True


def test_config_flag_disables_the_gate(conf, harness):
    harness.write_config({"gate": {"enabled": False}})
    cfg = conf.load_config()
    assert conf.gate_enabled(cfg) is False


def test_gate_is_enabled_by_default(conf):
    assert conf.gate_enabled(conf.load_config()) is True


# ── Reading a config left behind by the pre-rename name ──────────────────────
#
# The plugin was called `task-cost-tracker` before it was called `abacus`, and its
# config lived in `~/.claude/task-cost-tracker/config.json`. State is disposable
# (state_store prunes it anyway), but config is not: a user who had set
# `gate.non_beads_project: block` would, on upgrade, silently drop back to `warn`
# — an enforcement regression that looks exactly like the plugin working.


@pytest.fixture
def default_paths(lib_path, harness, monkeypatch):
    """No `ABACUS_STATE_DIR`, so the default `$HOME/.claude/...` path resolves."""
    monkeypatch.delenv("ABACUS_STATE_DIR", raising=False)
    monkeypatch.delenv("ABACUS_DISABLE", raising=False)
    monkeypatch.setenv("HOME", str(harness.home))
    import abacus_config

    return abacus_config


def _write_legacy_config(harness, payload):
    legacy = harness.home / ".claude" / "task-cost-tracker"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "config.json").write_text(json.dumps(payload))
    return legacy


def test_a_config_left_in_the_pre_rename_directory_is_still_read(default_paths, harness):
    _write_legacy_config(harness, {"gate": {"non_beads_project": "block"}})
    cfg = default_paths.load_config()
    assert cfg["gate"]["non_beads_project"] == "block", (
        "a pre-rename config must not silently revert enforcement to 'warn'")


def test_the_current_config_wins_when_both_directories_have_one(default_paths, harness):
    _write_legacy_config(harness, {"ccusage_timeout_s": 11})
    current = harness.home / ".claude" / "abacus"
    current.mkdir(parents=True, exist_ok=True)
    (current / "config.json").write_text(json.dumps({"ccusage_timeout_s": 22}))
    assert default_paths.load_config()["ccusage_timeout_s"] == 22


def test_an_explicit_state_dir_suppresses_the_legacy_fallback(lib_path, harness, monkeypatch):
    """`ABACUS_STATE_DIR` means "look exactly here" — not "here, then elsewhere"."""
    monkeypatch.setenv("HOME", str(harness.home))
    monkeypatch.setenv("ABACUS_STATE_DIR", str(harness.state_dir))
    _write_legacy_config(harness, {"gate": {"non_beads_project": "block"}})
    import abacus_config

    assert abacus_config.load_config()["gate"]["non_beads_project"] == "warn"


def test_an_explicit_path_argument_is_never_second_guessed(default_paths, harness):
    """`load_config(path=...)` names a file; a missing one means defaults, not a search."""
    _write_legacy_config(harness, {"gate": {"non_beads_project": "block"}})
    cfg = default_paths.load_config(path=str(harness.tmp / "nope.json"))
    assert cfg["gate"]["non_beads_project"] == "warn"
