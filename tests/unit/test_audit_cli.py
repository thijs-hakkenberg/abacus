"""``hooks/scripts/audit.py`` driven as a real subprocess.

The detectors are tested as pure functions in ``test_audit.py``. What is tested
here is everything the CLI adds and only a subprocess can catch: that the bd read
happens once, that a failed read is reported as a failed read rather than as a
clean workspace, and — the part that matters most — exactly which
``--set-metadata`` flags ``--fix`` emits.

``--fix`` is the only code path in this plugin outside the hooks that writes to the
user's store of record, and it runs unattended. So the assertions are on the
recorded argv, not on a return value: what reaches ``bd`` is the contract.
"""

import json
import time

import pytest

from conftest import session_payload


def _hours_ago(hours):
    """A bd-style timestamp `hours` in the past.

    Relative, not a literal date: `stale_after_h` defaults to 24, so a fixture
    pinned to a wall-clock day starts reporting a `stale-claim` gap once the
    suite is run more than a day later — the default `issue()` is meant to be
    *unremarkable*, and a fixture whose meaning changes overnight is not. The
    tests that are genuinely about age pass an explicit date (`2026-01-01` for
    the stale claim) and the ones about duration pass an explicit pair.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))


def issue(issue_id, status="closed", metadata=None, started_at=None,
          closed_at=None, title="tracked work"):
    started_at = started_at or _hours_ago(2)
    closed_at = closed_at or _hours_ago(1)
    body = {
        "id": issue_id,
        "title": title,
        "status": status,
        "created_at": _hours_ago(3),
        "updated_at": closed_at,
        "started_at": started_at,
    }
    if status == "closed":
        body["closed_at"] = closed_at
    if metadata is not None:
        body["metadata"] = metadata
    return body


def run_audit(harness, issues=None, rc=0, args=(), **env):
    if issues is not None:
        harness.set_bd_json("list", issues, rc=rc)
    elif rc:
        harness.set_bd("list", "Error: no beads database found", rc=rc)
    harness.make_beads_project()
    return harness.run_hook(
        "audit.py", session_payload(), extra_args=tuple(args) + ("--json",), **env
    )


def report(result):
    assert result.rc == 0, "audit exited %s: %s" % (result.rc, result.stderr)
    return json.loads(result.stdout)


def writes(harness):
    """Every ``bd update ... --set-metadata`` as (issue_id, {key: value})."""
    out = []
    for call in harness.bd_calls():
        parts = call.split()
        if "--set-metadata" not in parts:
            continue
        pairs = {}
        for i, part in enumerate(parts):
            if part == "--set-metadata" and i + 1 < len(parts):
                key, _, value = parts[i + 1].partition("=")
                pairs[key] = value
        out.append((parts[2], pairs))
    return out


# ── Reading ─────────────────────────────────────────────────────────────────

def test_a_clean_workspace_reports_no_gaps(harness):
    data = report(run_audit(harness, [issue("ab-1", status="in_progress"),
                                      issue("ab-0", metadata={"abacus_schema": 1,
                                                              "abacus_partial": "false"})]))
    assert data["ok"] is True
    assert data["gaps"] == []


def test_a_closed_issue_with_no_attribution_is_reported(harness):
    data = report(run_audit(harness, [issue("ab-1", status="in_progress"), issue("ab-0")]))
    assert [g["issue_id"] for g in data["gaps"]] == ["ab-0"]
    assert data["gaps"][0]["kind"] == "unattributed"


def test_the_whole_workspace_is_read_in_one_bd_call(harness):
    """``bd list --all --json`` returns closed issues *and* their metadata.

    Per-status calls would multiply the subprocess cost, and a per-issue
    ``bd show`` for metadata would turn a 6-issue audit into 7 spawns.
    """
    run_audit(harness, [issue("ab-1", status="in_progress")])
    lists = [c for c in harness.bd_calls() if c.startswith("bd list")]
    assert len(lists) == 1
    assert "--all" in lists[0] and "--json" in lists[0]


def test_a_failed_bd_read_is_not_reported_as_a_clean_workspace(harness):
    """The distinction the whole plugin turns on: absent is not zero.

    ``{"ok": true, "gaps": []}`` from a workspace that could not be read tells the
    user their tracking is complete. It is the same class of wrong answer as a
    $0.00 cost estimate, and here it would talk someone out of investigating.
    """
    data = report(run_audit(harness, rc=1))
    assert data["ok"] is False
    assert data["reason"]
    assert "gaps" not in data or data["gaps"] == []


def test_a_missing_bd_is_reported_and_still_exits_zero(harness):
    harness.remove_bd()
    result = run_audit(harness)
    assert result.rc == 0
    assert report(result)["ok"] is False


def test_the_default_output_is_human_readable_not_json(harness):
    harness.set_bd_json("list", [issue("ab-1", status="in_progress"), issue("ab-0")])
    harness.make_beads_project()
    result = harness.run_hook("audit.py", session_payload())
    assert result.rc == 0
    assert not result.stdout.lstrip().startswith("{")
    assert "ab-0" in result.stdout


# ── Fixing ──────────────────────────────────────────────────────────────────

def test_a_clean_workspace_is_never_written_to(harness):
    run_audit(harness, [issue("ab-1", status="in_progress")], args=("--fix",))
    assert writes(harness) == []


def test_fixing_an_unattributed_issue_writes_no_dollar_figure(harness):
    """The rule this plugin exists to keep: a cost that cannot be read is omitted.

    A closed issue with no metadata has no ``abacus_session_id``, so there is no
    ccusage reading to recover. Writing ``0`` — or inventing a figure from the
    duration — would put a wrong number in the store of record permanently.
    """
    run_audit(harness, [issue("ab-1", status="in_progress"), issue("ab-0")],
              args=("--fix",))
    target, pairs = writes(harness)[0]
    assert target == "ab-0"
    assert pairs["abacus_cost_basis"] == "unavailable"
    assert "abacus_cost_usd_estimate" not in pairs
    assert not [k for k in pairs if k.startswith("abacus_tokens")]


def test_a_backfilled_write_says_that_it_was_backfilled(harness):
    """A figure reconstructed after the fact is weaker evidence than a measured one.

    Without this key, a later reader cannot tell an audited repair from a real
    claim/close measurement, and the two would average together in any report.
    """
    run_audit(harness, [issue("ab-1", status="in_progress"), issue("ab-0")],
              args=("--fix",))
    _, pairs = writes(harness)[0]
    assert pairs["abacus_backfilled"] == "true"


def test_a_backfilled_write_records_the_duration_it_can_compute(harness):
    """Duration comes from bd's own timestamps, so it is always recoverable."""
    run_audit(harness, [issue("ab-1", status="in_progress"),
                        issue("ab-0", started_at="2026-08-31T09:00:00Z",
                              closed_at="2026-08-31T10:30:00Z")],
              args=("--fix",))
    _, pairs = writes(harness)[0]
    assert pairs["abacus_duration_min"] == "90"


def test_a_backfill_omits_a_duration_it_cannot_compute(harness):
    """``minutes_between`` answers 0 for an unparsable date. 0 is not the answer.

    A duration of zero against a task that ran all afternoon is the same lie as a
    $0.00 cost, so an unreadable timestamp omits the key rather than rounding down
    to nothing.
    """
    bad = issue("ab-0", started_at="whenever", closed_at="also whenever")
    bad["created_at"] = "nope"
    bad["updated_at"] = "nope"
    run_audit(harness, [issue("ab-1", status="in_progress"), bad], args=("--fix",))
    _, pairs = writes(harness)[0]
    assert "abacus_duration_min" not in pairs
    assert pairs["abacus_cost_basis"] == "unavailable"


def test_a_legacy_tct_issue_is_never_written_to(harness):
    """The pre-rename prefix is still attribution, and ``--fix`` runs unattended.

    Reading ``tct_*`` as absent would make every issue tracked before 0.3.0 look
    unattributed, and the repair would overwrite real recorded figures with
    ``unavailable`` — the audit destroying the data it exists to protect.
    """
    run_audit(harness, [
        issue("ab-1", status="in_progress"),
        issue("ab-0", metadata={"tct_schema": 1, "tct_partial": "false",
                                "tct_cost_basis": "ccusage-local-list-rate",
                                "tct_cost_usd_estimate": "1.25"}),
    ], args=("--fix",))
    assert writes(harness) == []


def test_finalising_a_partial_keeps_the_figures_already_banked(harness):
    """The spend was really measured once. Finalising must not discard it.

    Overwriting a banked $2.50 with ``unavailable`` would lose the only record of
    what that work cost — a repair that destroys data.
    """
    run_audit(harness, [
        issue("ab-1", status="in_progress"),
        issue("ab-0", metadata={
            "abacus_schema": 1,
            "abacus_partial": "true",
            "abacus_cost_basis": "ccusage-local-list-rate",
            "abacus_cost_usd_estimate": "2.5",
            "abacus_tokens_total": "4000",
        }),
    ], args=("--fix",))
    _, pairs = writes(harness)[0]
    assert pairs["abacus_cost_usd_estimate"] == "2.5"
    assert pairs["abacus_tokens_total"] == "4000"
    assert pairs["abacus_cost_basis"] == "ccusage-local-list-rate"
    assert pairs["abacus_partial"] == "false"


def test_an_unknown_schema_version_is_never_written_to(harness):
    """A richer shape written by a newer version must not be flattened by this one."""
    run_audit(harness, [issue("ab-1", status="in_progress"),
                        issue("ab-0", metadata={"abacus_schema": 99})],
              args=("--fix",))
    assert writes(harness) == []


def test_a_stale_claim_is_reported_but_never_written_to(harness):
    """What to do about a stale claim is a judgement about intent, not a repair.

    Closing it unattended would mark work done that is not done; finalising it
    would bank a cost against a task still running. The audit reports and stops.
    """
    data = report(run_audit(harness, [
        issue("ab-1", status="in_progress", started_at="2026-01-01T09:00:00Z"),
    ], args=("--fix",)))
    assert [g["kind"] for g in data["gaps"]] == ["stale-claim"]
    assert writes(harness) == []


def test_the_report_says_what_it_fixed(harness):
    data = report(run_audit(harness, [issue("ab-1", status="in_progress"), issue("ab-0")],
                            args=("--fix",)))
    assert data["fixed"] == ["ab-0"]


def test_fix_reports_a_write_that_bd_rejected(harness):
    """A silent failure here means the user believes a gap was closed when it was not."""
    harness.set_bd_json("list", [issue("ab-1", status="in_progress"), issue("ab-0")])
    harness.set_bd("update", "", rc=1)
    harness.make_beads_project()
    result = harness.run_hook("audit.py", session_payload(), extra_args=("--fix", "--json"))
    data = report(result)
    assert data["fixed"] == []
    assert data["fix_failed"] == ["ab-0"]


# ── Failing open ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [(), ("--json",), ("--fix", "--json")])
def test_the_script_exits_zero_whatever_it_is_asked(harness, args):
    """It is not a hook, but it runs inside an agent turn; a non-zero exit there
    reads as a tool failure and derails the turn for a condition that is merely
    "nothing to report"."""
    harness.set_bd("list", "not json at all", rc=0)
    harness.make_beads_project()
    assert harness.run_hook("audit.py", session_payload(), extra_args=args).rc == 0


def test_a_project_with_no_beads_workspace_says_so(harness):
    data = report(harness.run_hook("audit.py", session_payload(), extra_args=("--json",)))
    assert data["ok"] is False
    assert "workspace" in data["reason"].lower()
