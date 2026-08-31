"""The audit detectors: where work and attribution came apart.

These are pure functions over the list ``bd list --all --json`` returns, plus a
list of commits. Keeping them free of subprocesses is what makes the interesting
cases — a legacy prefix, a missing timestamp, a malformed metadata blob —
testable at all; the CLI that feeds them is tested separately as a real
subprocess in ``test_audit_cli.py``.

Every detector answers a question of the form "is this issue missing something it
should have". The recurring hazard is the false positive: an issue reported as a
gap when it is fine costs the user a pointless write to their store of record, and
under ``--fix`` that write happens without them looking. So the boundary tests
here outnumber the happy paths, and each one names the wrong answer it prevents.
"""

import pytest

NOW = "2026-08-31T12:00:00Z"


@pytest.fixture
def audit(lib_path):
    import audit as module

    return module


def issue(issue_id, status="closed", metadata=None, started_at=None,
          closed_at="2026-08-31T10:00:00Z", title="tracked work"):
    body = {
        "id": issue_id,
        "title": title,
        "status": status,
        "created_at": "2026-08-31T09:00:00Z",
        "updated_at": closed_at or "2026-08-31T09:00:00Z",
        "started_at": started_at if started_at is not None else "2026-08-31T09:30:00Z",
    }
    if status == "closed":
        body["closed_at"] = closed_at
    if metadata is not None:
        body["metadata"] = metadata
    return body


def attributed(**over):
    meta = {
        "abacus_schema": 1,
        "abacus_partial": False,
        "abacus_cost_basis": "ccusage-local-list-rate",
        "abacus_cost_usd_estimate": 0.5,
        "abacus_session_id": "sess-1",
    }
    meta.update(over)
    return meta


def kinds(gaps):
    return [g["kind"] for g in gaps]


def of_kind(gaps, kind):
    return [g for g in gaps if g["kind"] == kind]


# ── Nothing claimed right now ───────────────────────────────────────────────

def test_an_empty_workspace_reports_that_nothing_is_claimed(audit):
    gaps = audit.audit([], now=NOW)
    assert audit.KIND_UNCLAIMED in kinds(gaps)


def test_a_claim_in_progress_means_nothing_is_unclaimed(audit):
    gaps = audit.audit([issue("ab-1", status="in_progress")], now=NOW)
    assert audit.KIND_UNCLAIMED not in kinds(gaps)


def test_only_an_in_progress_issue_counts_as_a_claim(audit):
    """An open issue is not a claim. The gate reads ``--status in_progress``.

    Counting `open` here would report the gate as satisfied while it is still
    denying every edit — a report that contradicts the software it describes.
    """
    gaps = audit.audit([issue("ab-1", status="open")], now=NOW)
    assert audit.KIND_UNCLAIMED in kinds(gaps)


# ── Stale claims ────────────────────────────────────────────────────────────

def test_a_claim_older_than_the_threshold_is_stale(audit):
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", started_at="2026-08-29T09:00:00Z")],
        now=NOW, stale_after_h=24,
    )
    assert [g["issue_id"] for g in of_kind(gaps, audit.KIND_STALE_CLAIM)] == ["ab-1"]


def test_a_fresh_claim_is_not_stale(audit):
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", started_at="2026-08-31T11:00:00Z")],
        now=NOW, stale_after_h=24,
    )
    assert not of_kind(gaps, audit.KIND_STALE_CLAIM)


def test_a_claim_with_no_start_time_is_never_called_stale(audit):
    """bd may not have recorded ``started_at``. Absence is not evidence of age.

    Reporting it stale would invite closing a task claimed a minute ago, on the
    strength of a field that was never written.
    """
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", started_at=None)], now=NOW, stale_after_h=24,
    )
    assert not of_kind(gaps, audit.KIND_STALE_CLAIM)


def test_an_unparseable_start_time_is_never_called_stale(audit):
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", started_at="whenever")], now=NOW,
    )
    assert not of_kind(gaps, audit.KIND_STALE_CLAIM)


# ── Closed but never finalised ──────────────────────────────────────────────

def test_a_closed_issue_left_partial_is_unfinalised(audit):
    gaps = audit.audit([issue("ab-1", metadata=attributed(abacus_partial=True))], now=NOW)
    assert [g["issue_id"] for g in of_kind(gaps, audit.KIND_UNFINALISED)] == ["ab-1"]


def test_a_partial_flag_stored_as_a_string_still_counts(audit):
    """bd round-trips metadata values as strings, so ``"true"`` is the real shape.

    Testing only the boolean would pass while the detector missed every gap that
    has ever existed on disk.
    """
    gaps = audit.audit([issue("ab-1", metadata=attributed(abacus_partial="true"))], now=NOW)
    assert of_kind(gaps, audit.KIND_UNFINALISED)


def test_a_finalised_issue_is_not_reported(audit):
    gaps = audit.audit([issue("ab-1", metadata=attributed(abacus_partial="false"))], now=NOW)
    assert not of_kind(gaps, audit.KIND_UNFINALISED)
    assert not of_kind(gaps, audit.KIND_UNATTRIBUTED)


def test_an_in_progress_issue_is_never_unfinalised(audit):
    """A task still running has no final figure to be missing. That is not a gap."""
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", metadata=attributed(abacus_partial=True))],
        now=NOW,
    )
    assert not of_kind(gaps, audit.KIND_UNFINALISED)


# ── Closed with no attribution at all ───────────────────────────────────────

def test_a_closed_issue_with_no_metadata_is_unattributed(audit):
    gaps = audit.audit([issue("ab-1")], now=NOW)
    assert [g["issue_id"] for g in of_kind(gaps, audit.KIND_UNATTRIBUTED)] == ["ab-1"]


def test_legacy_tct_metadata_is_not_a_gap(audit):
    """The pre-rename prefix is still attribution. This is the shim's boundary.

    Without normalising, every issue tracked before 0.3.0 reports as unattributed,
    and ``--fix`` overwrites real recorded figures with ``unavailable`` — the audit
    destroying the very data it exists to protect.
    """
    gaps = audit.audit(
        [issue("ab-1", metadata={"tct_schema": 1, "tct_partial": "false",
                                 "tct_cost_basis": "ccusage-local-list-rate"})],
        now=NOW,
    )
    assert not of_kind(gaps, audit.KIND_UNATTRIBUTED)


def test_a_legacy_partial_is_reported_as_unfinalised(audit):
    gaps = audit.audit(
        [issue("ab-1", metadata={"tct_schema": 1, "tct_partial": "true"})], now=NOW,
    )
    assert of_kind(gaps, audit.KIND_UNFINALISED)


def test_metadata_that_is_not_a_dict_is_treated_as_absent(audit):
    """A malformed blob must not crash the audit, and must not read as attributed."""
    gaps = audit.audit([issue("ab-1", metadata="wat")], now=NOW)
    assert of_kind(gaps, audit.KIND_UNATTRIBUTED)


def test_an_unfinalised_issue_is_not_also_reported_unattributed(audit):
    """One issue, one gap. Two rows for one problem invites two writes."""
    gaps = audit.audit([issue("ab-1", metadata=attributed(abacus_partial=True))], now=NOW)
    assert not of_kind(gaps, audit.KIND_UNATTRIBUTED)


def test_an_unrecognised_schema_version_is_left_alone(audit):
    """``abacus_schema: 2`` is a shape this reader does not know.

    Reporting it as a gap would mean a future writer's richer metadata gets
    overwritten with ``unavailable`` by an older audit. The version field exists
    so a reader can decline.
    """
    gaps = audit.audit([issue("ab-1", metadata=attributed(abacus_schema=2))], now=NOW)
    assert not of_kind(gaps, audit.KIND_UNATTRIBUTED)
    assert not of_kind(gaps, audit.KIND_UNFINALISED)


# ── Work that never became a task ───────────────────────────────────────────

def commit(sha, when, subject="did a thing"):
    return {"sha": sha, "at": when, "subject": subject}


def test_a_commit_inside_a_tracked_window_is_not_untracked(audit):
    gaps = audit.audit(
        [issue("ab-1", started_at="2026-08-31T09:30:00Z", closed_at="2026-08-31T10:00:00Z")],
        now=NOW,
        commits=[commit("aaa", "2026-08-31T09:45:00Z")],
    )
    assert not of_kind(gaps, audit.KIND_UNTRACKED_COMMITS)


def test_a_commit_outside_every_window_is_untracked(audit):
    gaps = audit.audit(
        [issue("ab-1", started_at="2026-08-31T09:30:00Z", closed_at="2026-08-31T10:00:00Z")],
        now=NOW,
        commits=[commit("bbb", "2026-08-31T11:30:00Z")],
    )
    found = of_kind(gaps, audit.KIND_UNTRACKED_COMMITS)
    assert found and found[0]["shas"] == ["bbb"]


def test_an_in_progress_claim_covers_commits_up_to_now(audit):
    """A claim that has not closed yet is an open window, not a zero-width one."""
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", started_at="2026-08-31T09:30:00Z")],
        now=NOW,
        commits=[commit("ccc", "2026-08-31T11:30:00Z")],
    )
    assert not of_kind(gaps, audit.KIND_UNTRACKED_COMMITS)


def test_a_commit_with_an_unreadable_timestamp_is_not_reported(audit):
    """Cannot place it in time, so cannot say it was untracked. Stay quiet."""
    gaps = audit.audit([], now=NOW, commits=[commit("ddd", "not-a-date")])
    assert not of_kind(gaps, audit.KIND_UNTRACKED_COMMITS)


def test_untracked_commits_are_reported_as_one_gap(audit):
    """Twenty commits from one untracked afternoon are one problem, not twenty.

    Per-commit rows would make the report unreadable and, worse, suggest twenty
    issues should be created.
    """
    gaps = audit.audit(
        [], now=NOW,
        commits=[commit("a", "2026-08-31T11:00:00Z"), commit("b", "2026-08-31T11:05:00Z")],
    )
    found = of_kind(gaps, audit.KIND_UNTRACKED_COMMITS)
    assert len(found) == 1
    assert found[0]["shas"] == ["a", "b"]


# ── The report as a whole ───────────────────────────────────────────────────

def test_a_clean_workspace_reports_no_gaps(audit):
    gaps = audit.audit(
        [issue("ab-1", status="in_progress", started_at="2026-08-31T11:00:00Z"),
         issue("ab-0", metadata=attributed())],
        now=NOW,
    )
    assert gaps == []


def test_every_gap_says_whether_the_script_can_fix_it(audit):
    """``fixable`` is what ``--fix`` acts on, so absence of the key is a bug.

    The two metadata gaps have exactly one correct repair and are fixable. Nothing
    else is: what to do about a stale claim or an untracked commit is a judgement,
    and the script does not make judgements.
    """
    gaps = audit.audit(
        [issue("ab-1", metadata=attributed(abacus_partial=True)),
         issue("ab-2"),
         issue("ab-3", status="in_progress", started_at="2026-08-01T09:00:00Z")],
        now=NOW,
        # Before every window: the stale claim's window runs from its start to
        # *now*, so a commit dated today would be covered by it.
        commits=[commit("a", "2026-07-01T11:00:00Z")],
    )
    by_kind = {g["kind"]: g["fixable"] for g in gaps}
    assert by_kind[audit.KIND_UNFINALISED] is True
    assert by_kind[audit.KIND_UNATTRIBUTED] is True
    assert by_kind[audit.KIND_STALE_CLAIM] is False
    assert by_kind[audit.KIND_UNTRACKED_COMMITS] is False


def test_every_gap_carries_a_title_and_a_human_detail(audit):
    gaps = audit.audit([issue("ab-1", title="fix the retry backoff")], now=NOW)
    gap = of_kind(gaps, audit.KIND_UNATTRIBUTED)[0]
    assert gap["title"] == "fix the retry backoff"
    assert gap["detail"] and isinstance(gap["detail"], str)


def test_gaps_are_ordered_deterministically(audit):
    """The report is diffed and pasted into tickets; unstable order is noise."""
    issues = [issue("ab-2"), issue("ab-1"), issue("ab-3")]
    first = audit.audit(issues, now=NOW)
    second = audit.audit(list(reversed(issues)), now=NOW)
    assert [g["issue_id"] for g in first] == [g["issue_id"] for g in second]
