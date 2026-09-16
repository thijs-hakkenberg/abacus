"""Coverage: attribution.build_metadata / finalise / backfill_metadata.

``hooks/lib/attribution.py`` had no dedicated unit test file before this one —
its build/finalise/backfill paths were only exercised indirectly, through the
watcher's and audit's own tests (which is why the naive, subprocess-blind
coverage run showed it at 52%). These are direct, in-process tests of the pure
functions: ``beads``, ``ccusage`` and ``otel`` are monkeypatched here, so
nothing shells out and nothing touches the real ``$HOME`` — attribution.py
itself never spawns a subprocess (that is ``beads``/``ccusage``'s job), so
monkeypatching those sibling modules is enough to keep this file's tests
hermetic without needing the ``harness`` fixture at all.

``build_commit_edges``/``commit_edges`` already have their own file,
``test_commit_edges.py``; this one covers everything else in the module.
"""

import pytest


@pytest.fixture
def attribution(lib_path):
    import attribution as module

    return module


def zero_snapshot(ok=True):
    """Exactly what ``ccusage.snapshot`` returns for a session it has not
    seen usage for yet (adr/003's documented "brand new session" shape)."""
    return {"cost": 0.0, "tokens": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0, "models": [],
            "ok": ok}


def snapshot(cost, tokens, ok=True, models=None, **extra):
    return {
        "cost": cost, "tokens": tokens,
        "input_tokens": extra.get("input_tokens", 0),
        "output_tokens": extra.get("output_tokens", 0),
        "cache_read_tokens": extra.get("cache_read_tokens", 0),
        "cache_creation_tokens": extra.get("cache_creation_tokens", 0),
        "models": models or [], "ok": ok,
    }


def _patch_beads_show(monkeypatch, attribution, metadata=None):
    """`beads.show` as `carried_partial` sees it: an issue, or None."""
    result = {"metadata": metadata} if metadata is not None else None
    monkeypatch.setattr(attribution.beads, "show",
                         lambda issue_id, cwd=None: result)


def _patch_close_read(monkeypatch, attribution, after):
    monkeypatch.setattr(attribution.ccusage, "snapshot",
                         lambda session, cfg, fresh=False: after)


# ── _number / _is_true ──────────────────────────────────────────────────────


def test_number_parses_numeric_strings(attribution):
    assert attribution._number("3.5") == 3.5


def test_number_falls_back_to_default_on_unparsable(attribution):
    assert attribution._number("not-a-number") == 0
    assert attribution._number(None, default=7) == 7


def test_is_true_accepts_bool_and_string_forms(attribution):
    assert attribution._is_true(True) is True
    assert attribution._is_true("true") is True
    assert attribution._is_true("True") is True
    assert attribution._is_true(False) is False
    assert attribution._is_true("false") is False
    assert attribution._is_true(None) is False


# ── _normalise_prefix ────────────────────────────────────────────────────────


def test_normalise_prefix_rewrites_legacy_keys(attribution):
    out = attribution._normalise_prefix({"tct_partial": "true", "tct_cost_basis": "x"})
    assert out == {"abacus_partial": "true", "abacus_cost_basis": "x"}


def test_normalise_prefix_current_key_wins_over_legacy(attribution):
    out = attribution._normalise_prefix({"tct_partial": "true", "abacus_partial": "false"})
    assert out == {"abacus_partial": "false"}


# ── carried_partial ──────────────────────────────────────────────────────────


def test_carried_partial_empty_when_issue_missing(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution, metadata=None)
    assert attribution.carried_partial("t-1") == {}


def test_carried_partial_empty_when_metadata_not_a_dict(attribution, monkeypatch):
    monkeypatch.setattr(attribution.beads, "show",
                         lambda issue_id, cwd=None: {"metadata": "oops"})
    assert attribution.carried_partial("t-1") == {}


def test_carried_partial_empty_when_flag_false(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution, metadata={"abacus_partial": False})
    assert attribution.carried_partial("t-1") == {}


def test_carried_partial_reads_legacy_flag_when_current_absent(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution,
                       metadata={"tct_partial": "true", "tct_cost_usd_estimate": "1.5"})
    out = attribution.carried_partial("t-1")
    assert out == {"abacus_partial": "true", "abacus_cost_usd_estimate": "1.5"}


def test_carried_partial_current_flag_overrides_legacy(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution,
                       metadata={"tct_partial": "true", "abacus_partial": False})
    assert attribution.carried_partial("t-1") == {}


# ── build_metadata: cost basis ───────────────────────────────────────────────


def test_build_metadata_unavailable_when_no_baseline(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    state = {"snapshot": None, "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_cost_basis"] == attribution.BASIS_UNAVAILABLE
    assert "abacus_cost_usd_estimate" not in meta
    assert meta["abacus_duration_min"] == 10


def test_build_metadata_unavailable_when_baseline_not_ok(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    state = {"snapshot": zero_snapshot(ok=False), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_cost_basis"] == attribution.BASIS_UNAVAILABLE


def test_build_metadata_unavailable_when_fresh_read_fails(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, {"ok": False})
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_cost_basis"] == attribution.BASIS_UNAVAILABLE


def test_build_metadata_writes_cost_and_tokens_when_readable(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution,
                       snapshot(2.5, 1000, models=["claude-opus-5"],
                                input_tokens=400, output_tokens=600))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_cost_basis"] == attribution.COST_BASIS
    assert meta["abacus_cost_usd_estimate"] == 2.5
    assert meta["abacus_tokens_total"] == 1000
    assert meta["abacus_tokens_in"] == 400
    assert meta["abacus_tokens_out"] == 600
    assert meta["abacus_models"] == "claude-opus-5"
    assert meta["abacus_duration_min"] == 10


def test_build_metadata_joins_multiple_models(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution,
                       snapshot(1.0, 10, models=["claude-opus-5", "claude-haiku-4-5"]))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_models"] == "claude-opus-5,claude-haiku-4-5"


def test_build_metadata_omits_models_key_when_delta_has_none(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10, models=[]))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert "abacus_models" not in meta


def test_build_metadata_accumulates_onto_carried_partial(attribution, monkeypatch):
    carried_meta = {"abacus_partial": True, "abacus_cost_usd_estimate": 1.0,
                     "abacus_tokens_total": 500, "abacus_duration_min": 5}
    _patch_beads_show(monkeypatch, attribution, metadata=carried_meta)
    _patch_close_read(monkeypatch, attribution, snapshot(0.5, 200))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_cost_usd_estimate"] == 1.5
    assert meta["abacus_tokens_total"] == 700
    assert meta["abacus_duration_min"] == 15


def test_build_metadata_partial_flag_reflects_argument(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    state = {"snapshot": None, "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {}, partial=True,
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_partial"] is True


# ── build_metadata: OTEL enrichment ──────────────────────────────────────────


def test_build_metadata_adds_otel_stats_when_activity_present(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10))
    monkeypatch.setattr(
        attribution.otel, "window_stats",
        lambda session, start, end, path=None: {"tool_calls": 4, "active_min": 3, "api_calls": 2})
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {"otel_enrichment": True},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_tool_calls"] == 4
    assert meta["abacus_active_min"] == 3


def test_build_metadata_otel_stats_accumulate_onto_carried(attribution, monkeypatch):
    carried_meta = {"abacus_partial": True, "abacus_tool_calls": 2, "abacus_active_min": 1}
    _patch_beads_show(monkeypatch, attribution, metadata=carried_meta)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10))
    monkeypatch.setattr(
        attribution.otel, "window_stats",
        lambda session, start, end, path=None: {"tool_calls": 4, "active_min": 3, "api_calls": 0})
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {"otel_enrichment": True},
                                       now="2023-11-14T22:10:00Z")
    assert meta["abacus_tool_calls"] == 6
    assert meta["abacus_active_min"] == 4


def test_build_metadata_skips_otel_when_no_activity_at_all(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10))
    monkeypatch.setattr(
        attribution.otel, "window_stats",
        lambda session, start, end, path=None: {"tool_calls": 0, "active_min": 0, "api_calls": 0})
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {"otel_enrichment": True},
                                       now="2023-11-14T22:10:00Z")
    assert "abacus_tool_calls" not in meta
    assert "abacus_active_min" not in meta


def test_build_metadata_skips_otel_when_not_enabled(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10))
    called = []
    monkeypatch.setattr(attribution.otel, "window_stats",
                         lambda *a, **k: called.append(1))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}
    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")
    assert not called
    assert "abacus_tool_calls" not in meta


def test_build_metadata_skips_otel_when_claimed_at_missing(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10))
    called = []
    monkeypatch.setattr(attribution.otel, "window_stats",
                         lambda *a, **k: called.append(1))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": None}
    meta = attribution.build_metadata("sess-1", "t-1", state, {"otel_enrichment": True},
                                       now="2023-11-14T22:10:00Z")
    assert not called


# ── finalise ──────────────────────────────────────────────────────────────


def test_finalise_writes_the_metadata_it_built(attribution, monkeypatch):
    monkeypatch.setattr(
        attribution.state_store, "load",
        lambda session: {"snapshot": zero_snapshot(ok=True),
                          "claimed_at": "2023-11-14T22:00:00Z"})
    _patch_beads_show(monkeypatch, attribution)
    _patch_close_read(monkeypatch, attribution, snapshot(1.0, 10))
    written = {}
    monkeypatch.setattr(
        attribution.beads, "set_metadata",
        lambda issue_id, pairs, cwd=None: written.setdefault(issue_id, pairs) or True)

    meta = attribution.finalise("sess-1", "t-1", {}, now="2023-11-14T22:10:00Z")

    assert written["t-1"] == meta
    assert meta["abacus_cost_basis"] == attribution.COST_BASIS


def test_finalise_logs_rather_than_raises_when_write_fails(attribution, monkeypatch):
    monkeypatch.setattr(attribution.state_store, "load",
                         lambda session: {"snapshot": None})
    _patch_beads_show(monkeypatch, attribution)
    monkeypatch.setattr(attribution.beads, "set_metadata",
                         lambda issue_id, pairs, cwd=None: False)
    logged = []
    monkeypatch.setattr("hook_io.log", lambda message: logged.append(message))

    meta = attribution.finalise("sess-1", "t-1", {})

    assert meta["abacus_cost_basis"] == attribution.BASIS_UNAVAILABLE
    assert logged and "t-1" in logged[0]


# ── _elapsed_min ──────────────────────────────────────────────────────────


def test_elapsed_min_none_when_start_unparsable(attribution):
    issue = {"started_at": "not-a-date", "closed_at": "2023-11-14T22:10:00Z"}
    assert attribution._elapsed_min(issue) is None


def test_elapsed_min_none_when_end_unparsable(attribution):
    issue = {"started_at": "2023-11-14T22:00:00Z", "closed_at": "not-a-date"}
    assert attribution._elapsed_min(issue) is None


def test_elapsed_min_falls_back_to_created_and_updated(attribution):
    issue = {"created_at": "2023-11-14T22:00:00Z", "updated_at": "2023-11-14T22:10:00Z"}
    assert attribution._elapsed_min(issue) == 10


def test_elapsed_min_falls_back_to_now_when_still_open(attribution):
    issue = {"started_at": "2023-11-14T22:00:00Z"}
    assert attribution._elapsed_min(issue, now="2023-11-14T22:20:00Z") == 20


# ── backfill_metadata ─────────────────────────────────────────────────────


def test_backfill_metadata_unavailable_when_nothing_carried(attribution):
    meta = attribution.backfill_metadata({"metadata": {}})
    assert meta["abacus_cost_basis"] == attribution.BASIS_UNAVAILABLE
    assert meta["abacus_backfilled"] is True
    assert meta["abacus_partial"] is False


def test_backfill_metadata_treats_non_dict_metadata_as_empty(attribution):
    meta = attribution.backfill_metadata({"metadata": "oops"})
    assert meta["abacus_cost_basis"] == attribution.BASIS_UNAVAILABLE


def test_backfill_metadata_keeps_a_banked_figure(attribution):
    carried = {"abacus_cost_basis": attribution.COST_BASIS, "abacus_cost_usd_estimate": 3.2,
               "abacus_tokens_total": 900, "abacus_models": "claude-opus-5",
               "abacus_session_id": "sess-9", "abacus_tool_calls": 5,
               "abacus_active_min": 2}
    issue = {"metadata": carried, "started_at": "2023-11-14T22:00:00Z",
             "closed_at": "2023-11-14T22:30:00Z"}

    meta = attribution.backfill_metadata(issue)

    assert meta["abacus_cost_basis"] == attribution.COST_BASIS
    assert meta["abacus_cost_usd_estimate"] == 3.2
    assert meta["abacus_tokens_total"] == 900
    assert meta["abacus_models"] == "claude-opus-5"
    assert meta["abacus_session_id"] == "sess-9"
    assert meta["abacus_tool_calls"] == 5
    assert meta["abacus_active_min"] == 2
    assert meta["abacus_duration_min"] == 30


def test_backfill_metadata_reads_a_banked_figure_via_legacy_prefix(attribution):
    carried = {"tct_cost_basis": attribution.COST_BASIS, "tct_cost_usd_estimate": 1.1}
    meta = attribution.backfill_metadata({"metadata": carried})
    assert meta["abacus_cost_basis"] == attribution.COST_BASIS
    assert meta["abacus_cost_usd_estimate"] == 1.1


def test_backfill_metadata_duration_falls_back_to_elapsed(attribution):
    issue = {"metadata": {}, "started_at": "2023-11-14T22:00:00Z",
             "closed_at": "2023-11-14T22:05:00Z"}
    meta = attribution.backfill_metadata(issue)
    assert meta["abacus_duration_min"] == 5


def test_backfill_metadata_omits_duration_when_unrecoverable(attribution):
    meta = attribution.backfill_metadata({"metadata": {}})
    assert "abacus_duration_min" not in meta


# ── clear_current ─────────────────────────────────────────────────────────


def test_clear_current_resets_tracking_fields(attribution, monkeypatch):
    written = {}
    monkeypatch.setattr(
        attribution.state_store, "update",
        lambda session, changes: written.update(changes) or changes)

    attribution.clear_current("sess-1", now="2023-11-14T22:30:00Z")

    assert written == {
        "current_task": "", "current_title": "", "snapshot": None,
        "closed_at": "2023-11-14T22:30:00Z",
    }


# ── a defect this file documents rather than fixes ──────────────────────────
#
# Reported upstream rather than patched here (out of scope for a coverage
# pass — this is a design decision about ccusage.snapshot's zero-baseline
# semantics, not a line this test file can close by adding an assertion).
#
# ccusage.snapshot()'s docstring says an ok=True all-zero reading means "a
# brand-new session ccusage has not seen yet", and that the caller may safely
# diff against it. That is true only when the session really is new. Nothing
# distinguishes that from a session ccusage simply has not indexed *yet* for
# some other session id it does not recognise (a transient lag, or — as
# happened by hand while probing this module for this task, session ids
# "test" and "x" against the real ccusage cache, since cleaned up) — in that
# case the "baseline" is a false zero, and build_metadata's diff against the
# close-time read attributes the *entire* session's accumulated cost to one
# task, silently, with a cost basis that presents it as a measurement. That
# is exactly the shape of lie adr/005 exists to refuse, arrived at from
# inside the diff rather than from an unreadable read.
#
# xfail(strict=True): documents the gap without failing the suite, and turns
# into a hard failure (XPASS) the day someone adds a guard — which is the
# point, since that day this test needs a real assertion instead.
@pytest.mark.xfail(
    strict=True,
    reason="build_metadata has no guard against a false all-zero baseline "
           "(ok=True, cost=0, tokens=0) being diffed against a close read "
           "that reflects real prior usage; see comment above this test.")
def test_a_false_zero_baseline_is_not_distinguished_from_a_real_one(attribution, monkeypatch):
    _patch_beads_show(monkeypatch, attribution)
    # The close-time read reflects a session that was NOT actually new — a
    # large prior balance the claim-time snapshot simply failed to see.
    _patch_close_read(monkeypatch, attribution, snapshot(249.16, 224826277))
    state = {"snapshot": zero_snapshot(ok=True), "claimed_at": "2023-11-14T22:00:00Z"}

    meta = attribution.build_metadata("sess-1", "t-1", state, {},
                                       now="2023-11-14T22:10:00Z")

    # A single short task must not be charged the whole session's cost. There
    # is currently no mechanism that would make this assertion pass.
    assert meta["abacus_cost_usd_estimate"] < 249.16
