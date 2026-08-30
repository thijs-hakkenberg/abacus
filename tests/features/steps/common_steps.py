"""Step definitions shared by every feature file.

Split by grammatical role rather than by feature, because the same Given ("the
working directory is a beads workspace") appears in all six files and pytest-bdd
resolves steps by text, not by which file they were written for. One definition
per sentence keeps the feature space honest: if two features word the same
precondition differently, that shows up here as two definitions and prompts the
wording to be unified.
"""

import json
import time

from pytest_bdd import given, parsers, then, when

from world import (COST_BASIS_LOCAL, COST_BASIS_UNAVAILABLE, TOKEN_KEYS,
                   baseline_snapshot, injected_context, issue, last_write,
                   merge_config, metadata_writes, otel_line, plant_ccusage_cache,
                   track_task, world, write_for)

# No `__all__` here on purpose: the binding module star-imports this one, and
# pytest-bdd discovers steps by scanning that module's namespace. An `__all__`
# would hide every step function and each scenario would fail with
# StepDefinitionNotFoundError. For the same reason step functions are named
# `step_*` rather than `_*` — a star import skips underscore-prefixed names.


# ══ Given: the workspace ════════════════════════════════════════════════════

@given("the working directory is a beads workspace")
def step_beads_workspace(world):
    world["harness"].make_beads_project()


@given("the working directory is not a beads workspace")
def step_no_beads_workspace(world):
    # The gate walks upward looking for .beads/, so a bare directory outside the
    # project tree is the only reliable way to express "not a workspace".
    plain = world["harness"].tmp / "plain"
    plain.mkdir(exist_ok=True)
    world["cwd"] = plain


# ══ Given: what beads reports ═══════════════════════════════════════════════

@given(parsers.parse('beads issue "{issue_id}" is in progress'))
def step_issue_in_progress(world, issue_id):
    world["harness"].set_bd_json("list", [issue(issue_id)])
    world["harness"].set_bd_json("show", [issue(issue_id)])


@given("no beads issue is in progress")
def step_nothing_in_progress(world):
    # An empty array with rc=0 — categorically different from bd failing, which
    # is the distinction the gate's fail-open behaviour rests on.
    world["harness"].set_bd_json("list", [])


@given(parsers.parse('beads issue "{issue_id}" has been closed outside this session'))
def step_closed_elsewhere(world, issue_id):
    world["harness"].set_bd_json("list", [])
    world["harness"].set_bd_json("show", [issue(issue_id, status="closed")])


@given(parsers.parse('beads issue "{issue_id}" has been moved back to open outside this session'))
def step_reopened_elsewhere(world, issue_id):
    world["harness"].set_bd_json("list", [])
    world["harness"].set_bd_json("show", [issue(issue_id, status="open")])


@given("the bd list command exits non-zero")
def step_bd_list_fails(world):
    world["harness"].set_bd("list", stdout="", rc=1)


@given("the bd prime command exits non-zero")
def step_bd_prime_fails(world):
    world["harness"].set_bd("prime", stdout="", rc=1)


@given("the bd update command exits non-zero")
def step_bd_update_fails(world):
    world["harness"].set_bd("update", stdout="Error: no such issue", rc=1)


@given("the bd dolt command exits non-zero")
def step_bd_dolt_fails(world):
    world["harness"].set_bd("dolt", stdout="fatal: could not read from remote", rc=1)


@given("the bd executable is not on PATH")
def step_no_bd(world):
    world["harness"].remove_bd()


@given("bd prime returns a workflow manual")
def step_bd_prime_manual(world):
    world["harness"].set_bd_json("prime", {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "BEADS WORKFLOW MANUAL: use bd ready, bd create, bd close ...",
    }})


# ══ Given: what ccusage reports ═════════════════════════════════════════════

@given(parsers.parse("ccusage reports a cumulative session cost of {cost:g} over {tokens:d} tokens"))
def step_ccusage_reports(world, cost, tokens):
    world["harness"].set_ccusage_session(
        world["session"], cost=cost, tokens=tokens,
        input_tokens=tokens // 4, output_tokens=tokens // 4,
        cache_read_tokens=tokens // 4, cache_creation_tokens=tokens // 4)


@given("ccusage cannot be read")
def step_ccusage_broken(world):
    world["harness"].set_ccusage_raw("", rc=1)


@given("ccusage does not respond in time")
def step_ccusage_hangs(world):
    world["harness"].set_ccusage_hang(3)
    world["env"]["ABACUS_CCUSAGE_TIMEOUT_S"] = 1


@given("the npx executable is not on PATH")
def step_no_npx(world):
    world["harness"].remove_npx()


@given("a ccusage reading was already cached for this session")
def step_ccusage_cached(world):
    # Deliberately the *baseline* figure: if the closing read honoured this cache
    # it would diff a value against itself and record the task as free.
    plant_ccusage_cache(world, cost=10.0, tokens=1000)


# ══ Given: session state ════════════════════════════════════════════════════

@given(parsers.parse('the task "{issue_id}" is being tracked with a cost baseline of {cost:g}'))
def step_tracking_task(world, issue_id, cost):
    track_task(world, issue_id, cost)
    world["harness"].set_bd_json("show", [issue(issue_id)])


@given("no task is being tracked in session state")
def step_not_tracking(world):
    world["harness"].write_state(world["session"], {"session_id": world["session"]})


@given(parsers.parse('"{issue_id}" already carries unfinished attribution of {cost:g} over {tokens:d} tokens'))
def step_carries_partial(world, issue_id, cost, tokens):
    step_carry(world, issue_id, cost, tokens, partial=True)


@given(parsers.parse('"{issue_id}" already carries finished attribution of {cost:g} over {tokens:d} tokens'))
def step_carries_final(world, issue_id, cost, tokens):
    step_carry(world, issue_id, cost, tokens, partial=False)


def step_carry(world, issue_id, cost, tokens, partial):
    world["harness"].set_bd_json("show", [issue(issue_id, status="closed", metadata={
        "abacus_partial": partial,
        "abacus_cost_usd_estimate": cost,
        "abacus_tokens_total": tokens,
        "abacus_duration_min": 30,
    })])


# ══ Given: configuration and payload ════════════════════════════════════════

@given("the gate is configured to block projects without a beads workspace")
def step_block_non_beads(world):
    merge_config(world, {"gate": {"non_beads_project": "block"}})


@given("the plugin is disabled by environment variable")
def step_disabled(world):
    world["env"]["ABACUS_DISABLE"] = "1"


@given(parsers.parse("the primer mode is configured as {mode:w}"))
def step_primer_mode(world, mode):
    merge_config(world, {"prime": {"mode": mode}})


@given("session end is configured to push")
def step_push_on_end(world):
    merge_config(world, {"sync_on_session_end": "push"})


@given("the payload marks the stop hook as already active")
def step_stop_active(world):
    world["payload_extra"]["stop_hook_active"] = True


@given("the payload is not valid JSON")
def step_bad_payload(world):
    world["raw_stdin"] = "this is not JSON at all"


@given("the event log holds no events for this session")
def step_empty_event_log(world):
    # Readable, and holding events — just none of them ours. This is the case
    # that once wrote `abacus_tool_calls: 0` onto a task that ran dozens of tools.
    events = world["harness"].tmp / "events.jsonl"
    events.write_text(otel_line("SOMEONE-ELSE", "tool_result", "2026-08-06T09:10:00Z"))
    merge_config(world, {"otel_events_path": str(events)})


# ══ Then: exit codes and silence ════════════════════════════════════════════

@then(parsers.parse("the hook exits with code {code:d}"))
def step_exit_code(world, code):
    result = world["result"]
    assert result.rc == code, "expected rc=%s, got %s (stderr: %s)" % (
        code, result.rc, result.stderr)
    assert "Traceback" not in result.stderr, "a hook must never surface a traceback"


@then("the gate prints nothing")
def step_prints_nothing(world):
    assert world["result"].stdout.strip() == "", (
        "stdout is the hook protocol; anything unexpected there is a decision "
        "the gate did not mean to make: %r" % world["result"].stdout)


@then("no context is injected")
def step_no_context(world):
    assert injected_context(world) == "", (
        "expected silence, got %r" % injected_context(world))


# ══ Then: the gate's decision ═══════════════════════════════════════════════

@then("the gate denies the tool call")
def step_denies(world):
    assert world["result"].permission_decision == "deny", (
        "expected a deny, got %r" % world["result"].stdout)
    assert world["result"].rc == 0, "the JSON is the decision; the exit code stays 0"


@then("the gate allows the tool call")
def step_allows(world):
    assert world["result"].permission_decision != "deny", (
        "unexpected deny: %r" % world["result"].reason)


@then(parsers.parse("the denial reason names the command to {intent}"))
def step_reason_names(world, intent):
    expected = {
        "claim an existing task": "bd update <id> --claim",
        "create a new task": 'bd create "<title>"',
    }[intent]
    assert expected in world["result"].reason, (
        "remediation must name %r; got: %s" % (expected, world["result"].reason))


@then("the denial reason names the command that initialises a beads workspace")
def step_reason_names_init(world):
    assert "bd init" in world["result"].reason


@then("the denial reason names the environment variable that bypasses the gate")
def step_reason_names_bypass(world):
    assert "ABACUS_DISABLE" in world["result"].reason


# ══ Then: session state ═════════════════════════════════════════════════════

@then(parsers.parse('session state records "{issue_id}" as the current task'))
def step_state_records_task(world, issue_id):
    state = world["harness"].read_state(world["session"]) or {}
    assert state.get("current_task") == issue_id, (
        "expected current_task=%s, got %r" % (issue_id, state.get("current_task")))


@then("no task is recorded in session state")
def step_state_records_nothing(world):
    state = world["harness"].read_state(world["session"]) or {}
    assert not state.get("current_task"), (
        "expected no tracked task, got %r" % state.get("current_task"))


@then(parsers.parse("session state records a cost baseline of {cost:g}"))
def step_state_records_baseline(world, cost):
    state = world["harness"].read_state(world["session"]) or {}
    snapshot = state.get("snapshot") or {}
    assert snapshot.get("cost") == cost, (
        "expected baseline %s, got %r" % (cost, snapshot.get("cost")))


@then(parsers.parse("the recorded cost baseline is attributed to {source}"))
def step_baseline_source(world, source):
    expected = {"the gate": "gate-lazy", "session start": "session-start-adopt"}[source]
    state = world["harness"].read_state(world["session"]) or {}
    assert state.get("snapshot_source") == expected, (
        "expected snapshot_source=%s, got %r" % (expected, state.get("snapshot_source")))


# ══ Then: subprocesses that must not happen ═════════════════════════════════

@then("ccusage is never invoked")
def step_no_ccusage(world):
    assert world["harness"].npx_calls() == [], (
        "this path must not spawn npx: %s" % world["harness"].npx_calls())


@then("beads is never invoked")
def step_no_beads(world):
    assert world["harness"].bd_calls() == [], (
        "this path must not spawn bd: %s" % world["harness"].bd_calls())


@then("beads is asked to push")
def step_pushes(world):
    assert any("dolt push" in c for c in world["harness"].bd_calls()), (
        "expected a `bd dolt push`; calls were %s" % world["harness"].bd_calls())


@then("beads is not asked to push")
def step_does_not_push(world):
    assert not any("dolt push" in c for c in world["harness"].bd_calls())


# ══ Then: the attribution write ═════════════════════════════════════════════

@then(parsers.parse('attribution is written to "{issue_id}"'))
def step_written_to(world, issue_id):
    write_for(world, issue_id)


@then("no attribution is written")
def step_nothing_written(world):
    assert metadata_writes(world) == [], (
        "expected no attribution write, got %s" % metadata_writes(world))


@then(parsers.parse('no cost estimate is written to "{issue_id}"'))
def step_no_cost_for(world, issue_id):
    for target, pairs in metadata_writes(world):
        if target == issue_id:
            assert "abacus_cost_usd_estimate" not in pairs, (
                "cost was charged to an issue this session never claimed")


@then(parsers.parse("the attribution records a cost estimate of {cost:g}"))
def step_records_cost(world, cost):
    written = last_write(world).get("abacus_cost_usd_estimate")
    assert written is not None, "no cost estimate was written"
    assert float(written) == cost, "expected %s, got %s" % (cost, written)


@then("the attribution records no cost estimate")
def step_records_no_cost(world):
    assert "abacus_cost_usd_estimate" not in last_write(world), (
        "an unreadable cost must be omitted, never written as zero")


@then("the attribution records the cost basis as a local list-rate estimate")
def step_basis_local(world):
    assert last_write(world).get("abacus_cost_basis") == COST_BASIS_LOCAL


@then("the attribution records the cost basis as unavailable")
def step_basis_unavailable(world):
    assert last_write(world).get("abacus_cost_basis") == COST_BASIS_UNAVAILABLE


@then("the recorded cost key is named as an estimate")
def step_cost_key_named_estimate(world):
    keys = [k for k in last_write(world) if "cost" in k and "basis" not in k]
    assert keys == ["abacus_cost_usd_estimate"], (
        "the key name is the label a reader sees first; it must say estimate: %s" % keys)


@then("the attribution records a total token count")
def step_records_total_tokens(world):
    assert "abacus_tokens_total" in last_write(world)


@then("the attribution records input, output, cache-read and cache-write token counts")
def step_records_every_token_dimension(world):
    meta = last_write(world)
    for key in TOKEN_KEYS:
        assert key in meta, "missing token dimension %s" % key


@then("the attribution records no token counts")
def step_records_no_tokens(world):
    meta = last_write(world)
    for key in TOKEN_KEYS:
        assert key not in meta, "%s written despite an unreadable cost" % key


@then("the attribution records a duration")
def step_records_duration(world):
    assert "abacus_duration_min" in last_write(world), (
        "elapsed time is knowable even when cost is not")


@then("the attribution records the schema version")
def step_records_schema(world):
    assert last_write(world).get("abacus_schema") == "1"


@then("the attribution records the session id")
def step_records_session(world):
    assert last_write(world).get("abacus_session_id") == world["session"]


@then("the attribution names the models used")
def step_records_models(world):
    assert last_write(world).get("abacus_models"), "no models recorded alongside the cost"


@then("the attribution records no tool-call count")
def step_records_no_tool_calls(world):
    meta = last_write(world)
    assert "abacus_tool_calls" not in meta, (
        "a zero here is indistinguishable from a measurement")
    assert "abacus_active_min" not in meta


@then("the attribution is marked finished")
def step_marked_finished(world):
    assert last_write(world).get("abacus_partial") == "false"


@then(parsers.parse('the attribution for "{issue_id}" is marked unfinished'))
def step_marked_unfinished_for(world, issue_id):
    assert write_for(world, issue_id).get("abacus_partial") == "true"


@then("the attribution is marked unfinished")
def step_marked_unfinished(world):
    assert last_write(world).get("abacus_partial") == "true"
