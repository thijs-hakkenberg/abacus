"""RED: per-session state file — atomic writes, tolerant reads, pruning."""

import json
import os
import time

import pytest


@pytest.fixture
def store(lib_path, harness, monkeypatch):
    monkeypatch.setenv("ABACUS_STATE_DIR", str(harness.state_dir))
    import state_store

    return state_store


def test_load_missing_session_returns_empty_dict(store):
    assert store.load("nope") == {}


def test_save_then_load_round_trips(store):
    store.save("s1", {"current_task": "bd-1", "snapshot": {"cost": 1.5}})
    got = store.load("s1")
    assert got["current_task"] == "bd-1"
    assert got["snapshot"]["cost"] == 1.5


def test_save_is_atomic_leaving_no_tmp_files(store, harness):
    store.save("s1", {"a": 1})
    leftovers = [p for p in os.listdir(harness.state_dir) if ".tmp" in p or p.endswith("~")]
    assert leftovers == []


def test_corrupt_state_file_reads_as_empty_not_raise(store, harness):
    (harness.state_dir / "session-bad.json").write_text("{not json")
    assert store.load("bad") == {}


def test_update_merges_into_existing_state(store):
    store.save("s1", {"current_task": "bd-1", "keep": True})
    store.update("s1", {"current_task": "bd-2"})
    got = store.load("s1")
    assert got["current_task"] == "bd-2"
    assert got["keep"] is True


def test_session_id_is_sanitised_into_the_filename(store, harness):
    # A hostile session_id must not escape the state dir (path jail).
    store.save("../../etc/passwd", {"a": 1})
    written = list(harness.state_dir.glob("session-*.json"))
    assert len(written) == 1
    assert ".." not in written[0].name


def test_prune_removes_files_older_than_max_age(store, harness):
    store.save("old", {"a": 1})
    store.save("new", {"a": 2})
    old = harness.state_dir / "session-old.json"
    stale = time.time() - (20 * 86400)
    os.utime(old, (stale, stale))

    removed = store.prune(max_age_days=14)

    assert "session-old.json" in [os.path.basename(p) for p in removed]
    assert not old.exists()
    assert (harness.state_dir / "session-new.json").exists()


def test_prune_never_touches_the_config_file(store, harness):
    harness.write_config({"gate": {"non_beads_project": "warn"}})
    cfg = harness.state_dir / "config.json"
    stale = time.time() - (99 * 86400)
    os.utime(cfg, (stale, stale))

    store.prune(max_age_days=14)

    assert cfg.exists(), "config.json is not session state and must survive pruning"


def test_state_dir_is_created_on_demand(lib_path, tmp_path, monkeypatch):
    target = tmp_path / "deep" / "nested" / "state"
    monkeypatch.setenv("ABACUS_STATE_DIR", str(target))
    import importlib

    import state_store

    importlib.reload(state_store)
    state_store.save("s1", {"a": 1})
    assert (target / "session-s1.json").exists()


def test_state_file_is_not_world_readable(store, harness):
    """State records what was worked on and when — keep it user-only."""
    store.save("s1", {"a": 1})
    mode = os.stat(harness.state_dir / "session-s1.json").st_mode & 0o777
    assert mode & 0o077 == 0, f"expected user-only perms, got {oct(mode)}"


def test_concurrent_saves_never_yield_a_partial_read(store, harness):
    """A reader must see either the old or the new doc, never a truncated one.

    Two hooks can run at once (a PostToolUse watcher and the PreToolUse gate of
    the next call), so the write must be rename-based rather than in-place.
    """
    store.save("s1", {"n": 0})
    for i in range(1, 60):
        store.save("s1", {"n": i, "padding": "x" * 4000})
        doc = json.loads((harness.state_dir / "session-s1.json").read_text())
        assert doc["n"] == i
