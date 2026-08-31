"""Steps for ``task-audit.feature`` — the audit script, driven as a subprocess.

The issue-planting Givens here **accumulate**: each one appends to the scenario's
workspace and rewrites the whole ``bd list`` stub, because the audit reads the
entire workspace in one call and a scenario needs to describe more than one issue
at a time. That is why they are worded "the workspace holds …" rather than reusing
``beads issue "X" is in progress``, which plants a one-issue listing and would
discard anything planted before it.

Timestamps for a stale claim are computed against the real clock rather than a
fixture date, because the audit takes ``now`` from the system clock; a hardcoded
2026 date would age into a false stale claim on its own.
"""

import json
import time

from pytest_bdd import given, parsers, then, when

from conftest import session_payload
from when_steps import step_run
from world import last_write, metadata_writes, world  # noqa: F401

_LOCAL_BASIS = "ccusage-local-list-rate"


def _hours_ago(hours):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))


def _plant(world, entry):
    """Append `entry` to the workspace and re-stub the whole listing."""
    issues = world.setdefault("audit_issues", [])
    issues.append(entry)
    world["harness"].set_bd_json("list", issues)


def _closed(issue_id, metadata=None):
    body = {
        "id": issue_id,
        "title": "tracked work",
        "status": "closed",
        "created_at": "2026-08-06T08:00:00Z",
        "started_at": "2026-08-06T09:00:00Z",
        "closed_at": "2026-08-06T10:30:00Z",
        "updated_at": "2026-08-06T10:30:00Z",
    }
    if metadata is not None:
        body["metadata"] = metadata
    return body


def report(world):
    result = world["result"]
    assert result.rc == 0, "audit exited %s: %s" % (result.rc, result.stderr)
    return json.loads(result.stdout)


def kinds_for(world, issue_id):
    return [g["kind"] for g in report(world).get("gaps", [])
            if g.get("issue_id") == issue_id]


# ══ Given: what the workspace holds ═════════════════════════════════════════

@given(parsers.parse('the workspace holds a closed issue "{issue_id}" with no attribution'))
def step_closed_unattributed(world, issue_id):
    _plant(world, _closed(issue_id))


@given(parsers.parse('the workspace holds a closed issue "{issue_id}" left unfinished '
                     'with a cost of {cost:g}'))
def step_closed_partial(world, issue_id, cost):
    _plant(world, _closed(issue_id, metadata={
        "abacus_schema": 1,
        "abacus_partial": "true",
        "abacus_cost_basis": _LOCAL_BASIS,
        "abacus_cost_usd_estimate": cost,
        "abacus_tokens_total": 4000,
    }))


@given(parsers.parse('the workspace holds a closed issue "{issue_id}" attributed '
                     'before the rename'))
def step_closed_legacy(world, issue_id):
    # The pre-0.3.0 prefix. Read as absent, every one of these would look
    # unattributed and --fix would overwrite a real figure with `unavailable`.
    _plant(world, _closed(issue_id, metadata={
        "tct_schema": 1,
        "tct_partial": "false",
        "tct_cost_basis": _LOCAL_BASIS,
        "tct_cost_usd_estimate": 1.25,
    }))


@given(parsers.parse('the workspace holds a closed issue "{issue_id}" with an '
                     'unrecognised attribution schema'))
def step_closed_future_schema(world, issue_id):
    _plant(world, _closed(issue_id, metadata={"abacus_schema": 99}))


@given(parsers.parse('the workspace holds a claim "{issue_id}" made {hours:d} hours ago'))
def step_open_claim(world, issue_id, hours):
    _plant(world, {
        "id": issue_id,
        "title": "tracked work",
        "status": "in_progress",
        "created_at": _hours_ago(hours + 1),
        "started_at": _hours_ago(hours),
        "updated_at": _hours_ago(hours),
    })


# ══ When ════════════════════════════════════════════════════════════════════

@when("the audit runs")
def step_run_audit(world):
    step_run(world, "audit.py",
             session_payload(session_id=world["session"], cwd=world["cwd"]),
             extra_args=("--json",))


@when("the audit runs with repairs enabled")
def step_run_audit_fix(world):
    step_run(world, "audit.py",
             session_payload(session_id=world["session"], cwd=world["cwd"]),
             extra_args=("--fix", "--json"))


# ══ Then: what the audit concluded ══════════════════════════════════════════

@then("the audit reports that nothing is claimed")
def step_reports_unclaimed(world):
    gaps = report(world).get("gaps", [])
    assert "unclaimed" in [g["kind"] for g in gaps], (
        "expected an unclaimed gap; got %s" % [g["kind"] for g in gaps])


@then(parsers.parse('the audit reports "{issue_id}" as unattributed'))
def step_reports_unattributed(world, issue_id):
    assert kinds_for(world, issue_id) == ["unattributed"], (
        "expected unattributed for %s; got %s" % (issue_id, kinds_for(world, issue_id)))


@then(parsers.parse('the audit reports "{issue_id}" as unfinished'))
def step_reports_unfinalised(world, issue_id):
    assert kinds_for(world, issue_id) == ["unfinalised"], (
        "expected unfinalised for %s; got %s" % (issue_id, kinds_for(world, issue_id)))


@then(parsers.parse('the audit reports "{issue_id}" as a stale claim'))
def step_reports_stale(world, issue_id):
    assert kinds_for(world, issue_id) == ["stale-claim"], (
        "expected stale-claim for %s; got %s" % (issue_id, kinds_for(world, issue_id)))


@then(parsers.parse('the audit reports no gap for "{issue_id}"'))
def step_reports_no_gap_for(world, issue_id):
    assert kinds_for(world, issue_id) == [], (
        "an ambiguous or already-attributed issue must not be reported as a gap: %s"
        % kinds_for(world, issue_id))


@then(parsers.parse("the audit reports {count:d} repairable gaps"))
def step_reports_repairable_count(world, count):
    assert report(world)["fixable"] == count, (
        "expected %d repairable, got %d" % (count, report(world)["fixable"]))


@then("the audit reports that it could not check")
def step_reports_could_not_check(world):
    data = report(world)
    assert data["ok"] is False, "a failed read must not report ok"
    assert data.get("reason"), "a failed read must say why"
    assert not data.get("gaps"), (
        "a workspace that could not be read must not be reportable as clean")


@then(parsers.parse('the audit reports that it could not repair "{issue_id}"'))
def step_reports_fix_failed(world, issue_id):
    data = report(world)
    assert issue_id in data["fix_failed"], (
        "a rejected write must be reported, not swallowed: %s" % data["fix_failed"])
    assert issue_id not in data["fixed"]


@then(parsers.parse("the audit exits with code {code:d}"))
def step_audit_exit_code(world, code):
    result = world["result"]
    assert result.rc == code, "expected rc=%s, got %s (stderr: %s)" % (
        code, result.rc, result.stderr)
    assert "Traceback" not in result.stderr


# ══ Then: the repair write ══════════════════════════════════════════════════

@then("the attribution is marked as backfilled")
def step_marked_backfilled(world):
    assert last_write(world).get("abacus_backfilled") == "true", (
        "a reconstruction must not be indistinguishable from a measurement")
