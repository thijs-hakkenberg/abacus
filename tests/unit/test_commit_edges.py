"""RED: task↔commit edges — the only constructor, and its reader.

``attribution.py`` is the single permitted place ``abacus_*`` keys are built, so the
commit edge is built here too rather than in the watcher that discovers it. These
tests pin the three things a second implementation would get wrong: the key shape,
the *tier* an edge is written at, and what a reader does with a value it cannot
parse.

Two properties are load-bearing beyond their appearance:

**Values must be whitespace-free by construction.** ``beads._metadata_token``
collapses whitespace to underscores to stop a value reaching bd as two argv words,
so a value containing a space would round-trip *changed* rather than fail. The
value is built to survive that, and the test asserts it, because a silent
mutation in the store of record is worse than a rejected write.

**An unreadable timestamp reads back as None, never as an epoch of zero.** A zero
would render as 1970-01-01 — a wrong answer wearing the costume of a measurement,
which is the failure mode adr/005 exists to refuse.
"""

import pytest


@pytest.fixture
def attribution(lib_path):
    import attribution as module

    return module


def commit(sha, at="2023-11-14T22:23:20Z", subject="work", declared=None):
    """A `gitlog.new_commits` entry, shaped as that function returns it."""
    return {"sha": sha, "at": at, "subject": subject,
            "declared": list(declared or [])}


SHA_A = "a1b2c3d4e5f67890abcdef1234567890abcdef12"
SHA_B = "0f1e2d3c4b5a69788796a5b4c3d2e1f009182736"
CLAIMED = "2023-11-14T22:00:00Z"


# ── build_commit_edges: the observed tier ───────────────────────────────────


def test_an_observed_edge_lands_on_the_claimed_task(attribution):
    edges = attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task="abacus-7", claimed_at=CLAIMED)

    assert list(edges) == ["abacus-7"]
    assert list(edges["abacus-7"]) == ["abacus_commit_a1b2c3d4e5f6"]


def test_the_edge_value_carries_basis_session_and_epoch(attribution):
    edges = attribution.build_commit_edges(
        [commit(SHA_A, at="2023-11-14T22:23:20Z")], "sess-1",
        current_task="abacus-7", claimed_at=CLAIMED)

    assert edges["abacus-7"]["abacus_commit_a1b2c3d4e5f6"] == \
        "observed:sess-1:1700000600"


def test_the_key_uses_twelve_hex_characters_of_the_sha(attribution):
    """Twelve, because that is what git abbreviates to in a repo this size.

    Long enough that a collision is not a practical concern, short enough that a
    reader can match it against ``git log --oneline`` by eye.
    """
    edges = attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task="abacus-7", claimed_at=CLAIMED)

    key = next(iter(edges["abacus-7"]))
    assert key == "abacus_commit_" + SHA_A[:12]
    assert len(key.split("_")[-1]) == 12


def test_several_commits_become_several_keys_on_one_task(attribution):
    """One edge, one key — the unit of write equals the unit of the fact."""
    edges = attribution.build_commit_edges(
        [commit(SHA_A), commit(SHA_B)], "sess-1",
        current_task="abacus-7", claimed_at=CLAIMED)

    assert sorted(edges["abacus-7"]) == [
        "abacus_commit_" + SHA_B[:12],
        "abacus_commit_" + SHA_A[:12],
    ]


def test_no_observed_edge_without_a_claimed_task(attribution):
    """HEAD moved with nothing claimed. There is no task to attribute it to."""
    assert attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task=None, claimed_at=None) == {}


def test_no_observed_edge_when_the_claim_time_is_unknown(attribution):
    """Rail 2 cannot be checked, so the edge is not written.

    An edge whose rail could not be evaluated is a guess, and the whole point of
    the ``observed`` tier is that it is not one.
    """
    assert attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task="abacus-7", claimed_at=None) == {}


def test_a_commit_older_than_the_claim_is_not_observed(attribution):
    """Rail 2. This is what makes ``git pull`` harmless.

    Fifty upstream commits move HEAD and every one of them predates the claim, so
    none can have been observed being made during it.
    """
    old = commit(SHA_A, at="2023-11-14T21:00:00Z")

    assert attribution.build_commit_edges(
        [old], "sess-1", current_task="abacus-7", claimed_at=CLAIMED) == {}


def test_a_commit_exactly_at_the_claim_instant_is_observed(attribution):
    """The boundary is inclusive: ``>=``, not ``>``.

    Timestamps here have one-second resolution, so a claim and a commit in the
    same second is ordinary rather than suspicious.
    """
    edges = attribution.build_commit_edges(
        [commit(SHA_A, at=CLAIMED)], "sess-1",
        current_task="abacus-7", claimed_at=CLAIMED)

    assert edges != {}


def test_a_commit_with_an_unparsable_timestamp_is_not_observed(attribution):
    assert attribution.build_commit_edges(
        [commit(SHA_A, at="not-a-time")], "sess-1",
        current_task="abacus-7", claimed_at=CLAIMED) == {}


def test_a_commit_with_no_sha_is_skipped(attribution):
    assert attribution.build_commit_edges(
        [commit("")], "sess-1", current_task="abacus-7", claimed_at=CLAIMED) == {}


def test_no_commits_is_no_edges(attribution):
    assert attribution.build_commit_edges(
        [], "sess-1", current_task="abacus-7", claimed_at=CLAIMED) == {}


# ── build_commit_edges: the declared tier ───────────────────────────────────


def test_a_declared_trailer_writes_to_the_named_task(attribution):
    edges = attribution.build_commit_edges(
        [commit(SHA_A, declared=["abacus-9"])], "sess-1",
        current_task="abacus-7", claimed_at=CLAIMED)

    assert list(edges) == ["abacus-9"]
    assert edges["abacus-9"]["abacus_commit_" + SHA_A[:12]].startswith("declared:")


def test_one_commit_declaring_three_tasks_writes_three_edges(attribution):
    """The m:n case, and the only tier that can express it."""
    edges = attribution.build_commit_edges(
        [commit(SHA_A, declared=["abacus-7", "abacus-8", "abacus-9"])],
        "sess-1", current_task=None, claimed_at=None)

    assert sorted(edges) == ["abacus-7", "abacus-8", "abacus-9"]
    key = "abacus_commit_" + SHA_A[:12]
    assert all(edges[i][key].startswith("declared:") for i in edges)


def test_a_declaration_needs_no_claim_at_all(attribution):
    """Declared evidence does not depend on abacus having watched anything."""
    edges = attribution.build_commit_edges(
        [commit(SHA_A, declared=["abacus-9"])], "sess-1",
        current_task=None, claimed_at=None)

    assert list(edges) == ["abacus-9"]


def test_a_declaration_is_honoured_even_for_a_commit_older_than_the_claim(
        attribution):
    """Rail 2 guards the *observed* tier only.

    A trailer is the author's own statement of what the commit belongs to; it does
    not become false because abacus was not watching when it was written.
    """
    edges = attribution.build_commit_edges(
        [commit(SHA_A, at="2023-11-14T21:00:00Z", declared=["abacus-9"])],
        "sess-1", current_task="abacus-7", claimed_at=CLAIMED)

    assert list(edges) == ["abacus-9"]


def test_a_declaration_supersedes_the_observed_edge_rather_than_adding_to_it(
        attribution):
    """Declared overrides. The claimed task gets no weaker, contradicting edge.

    Both statements would be defensible — the commit did land during that claim —
    but recording a second basis for the same commit turns an explicit
    declaration into one opinion among two.
    """
    edges = attribution.build_commit_edges(
        [commit(SHA_A, declared=["abacus-9"])], "sess-1",
        current_task="abacus-7", claimed_at=CLAIMED)

    assert "abacus-7" not in edges


def test_declared_and_observed_commits_in_one_boundary_both_land(attribution):
    edges = attribution.build_commit_edges(
        [commit(SHA_A, declared=["abacus-9"]), commit(SHA_B)],
        "sess-1", current_task="abacus-7", claimed_at=CLAIMED)

    assert sorted(edges) == ["abacus-7", "abacus-9"]
    assert list(edges["abacus-7"]) == ["abacus_commit_" + SHA_B[:12]]
    assert list(edges["abacus-9"]) == ["abacus_commit_" + SHA_A[:12]]


def test_two_commits_declaring_the_same_task_merge_into_one_entry(attribution):
    edges = attribution.build_commit_edges(
        [commit(SHA_A, declared=["abacus-9"]), commit(SHA_B, declared=["abacus-9"])],
        "sess-1", current_task=None, claimed_at=None)

    assert sorted(edges) == ["abacus-9"]
    assert len(edges["abacus-9"]) == 2


# ── the value survives the argv round trip ──────────────────────────────────


def test_the_value_contains_no_whitespace(attribution):
    """``beads._metadata_token`` would collapse whitespace to underscores.

    That is a silent mutation rather than a failure, so the value is built to
    contain none in the first place.
    """
    edges = attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task="abacus-7", claimed_at=CLAIMED)

    value = edges["abacus-7"]["abacus_commit_" + SHA_A[:12]]
    assert value == "_".join(value.split())


def test_a_session_id_containing_whitespace_or_colons_is_sanitised(attribution):
    """A colon in the session would add a field the reader cannot resolve.

    The session id is not ours to validate at its source, so it is neutralised
    here rather than trusted.
    """
    edges = attribution.build_commit_edges(
        [commit(SHA_A)], "weird: id\there",
        current_task="abacus-7", claimed_at=CLAIMED)

    value = edges["abacus-7"]["abacus_commit_" + SHA_A[:12]]
    assert value.count(":") == 2
    assert value == "_".join(value.split())


def test_the_key_survives_the_metadata_token_renderer_unchanged(attribution,
                                                               lib_path):
    import beads

    edges = attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task="abacus-7", claimed_at=CLAIMED)
    value = edges["abacus-7"]["abacus_commit_" + SHA_A[:12]]

    assert beads._metadata_token(value) == value


# ── commit_edges: the reader ────────────────────────────────────────────────


def test_commit_edges_reads_back_what_was_written(attribution):
    written = attribution.build_commit_edges(
        [commit(SHA_A)], "sess-1", current_task="abacus-7", claimed_at=CLAIMED)

    got = attribution.commit_edges(written["abacus-7"])

    assert got == [{"sha12": SHA_A[:12], "basis": "observed",
                    "session": "sess-1", "at": "2023-11-14T22:23:20Z"}]


def test_commit_edges_ignores_every_other_metadata_key(attribution):
    meta = {"abacus_schema": 1, "abacus_cost_basis": "unavailable",
            "abacus_commit_a1b2c3d4e5f6": "observed:sess-1:1700000600"}

    assert [e["sha12"] for e in attribution.commit_edges(meta)] == ["a1b2c3d4e5f6"]


def test_commit_edges_returns_chronological_order(attribution):
    meta = {
        "abacus_commit_bbbbbbbbbbbb": "observed:sess-1:1700000600",
        "abacus_commit_aaaaaaaaaaaa": "observed:sess-1:1700000000",
    }

    assert [e["sha12"] for e in attribution.commit_edges(meta)] == \
        ["a" * 12, "b" * 12]


def test_commit_edges_reads_the_legacy_prefix(attribution):
    """A task written before the rename must not lose its edges.

    Read-only, exactly as ``_normalise_prefix`` is used everywhere else; nothing
    writes ``tct_`` again.
    """
    meta = {"tct_commit_a1b2c3d4e5f6": "observed:old-sess:1700000600"}

    got = attribution.commit_edges(meta)

    assert [e["sha12"] for e in got] == ["a1b2c3d4e5f6"]
    assert got[0]["session"] == "old-sess"


def test_a_current_key_wins_over_the_legacy_one_for_the_same_sha(attribution):
    meta = {
        "tct_commit_a1b2c3d4e5f6": "observed:old-sess:1700000000",
        "abacus_commit_a1b2c3d4e5f6": "declared:new-sess:1700000600",
    }

    got = attribution.commit_edges(meta)

    assert len(got) == 1
    assert got[0]["basis"] == "declared"


def test_commit_edges_skips_a_value_it_cannot_parse(attribution):
    meta = {"abacus_commit_a1b2c3d4e5f6": "garbage",
            "abacus_commit_bbbbbbbbbbbb": "observed:sess-1:1700000600"}

    assert [e["sha12"] for e in attribution.commit_edges(meta)] == ["b" * 12]


def test_commit_edges_reports_an_unreadable_epoch_as_none_not_as_1970(attribution):
    """The adr/005 rule applied to a timestamp.

    An epoch of zero would render as 1970-01-01 in a report and read as a
    measurement. None reads as "unknown", which is what it is.
    """
    meta = {"abacus_commit_a1b2c3d4e5f6": "observed:sess-1:not-a-number"}

    got = attribution.commit_edges(meta)

    assert len(got) == 1
    assert got[0]["at"] is None


def test_commit_edges_skips_a_key_that_is_not_a_sha(attribution):
    meta = {"abacus_commit_nothexatall": "observed:sess-1:1700000600",
            "abacus_commit_a1b2c3d4e5f6": "observed:sess-1:1700000600"}

    assert [e["sha12"] for e in attribution.commit_edges(meta)] == ["a1b2c3d4e5f6"]


def test_commit_edges_tolerates_a_session_id_holding_no_colon_at_all(attribution):
    """Three fields minimum; a two-field value is not ours and is skipped."""
    assert attribution.commit_edges({"abacus_commit_a1b2c3d4e5f6": "observed:1700"}) \
        == []


def test_commit_edges_of_nothing_is_nothing(attribution):
    assert attribution.commit_edges({}) == []
    assert attribution.commit_edges(None) == []
    assert attribution.commit_edges("not a dict") == []
