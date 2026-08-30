"""RED: timestamp helpers.

Every duration this plugin records passes through here, so the failure modes that
matter are the quiet ones: a format that parses to None makes a task look
zero-minutes long rather than raising anything.
"""

import time

import pytest


@pytest.fixture
def abacus_time(lib_path):
    import abacus_time as module

    return module


def test_now_is_iso_utc_with_a_z_suffix(abacus_time):
    stamp = abacus_time.now_iso()
    assert stamp.endswith("Z")
    assert abacus_time.parse_iso(stamp) == pytest.approx(time.time(), abs=5)


def test_now_can_be_pinned_for_deterministic_assertions(abacus_time, monkeypatch):
    monkeypatch.setenv("ABACUS_NOW", "2026-08-05T21:00:00Z")
    assert abacus_time.now_iso() == "2026-08-05T21:00:00Z"


def test_parses_the_format_claude_codes_otel_exporter_emits(abacus_time):
    """Fractional seconds and a Z suffix — the combination Python 3.9's own
    fromisoformat rejects, which is why this module exists."""
    assert abacus_time.parse_iso("2026-08-05T21:00:00.123Z") == pytest.approx(
        abacus_time.parse_iso("2026-08-05T21:00:00Z") + 0.123)


def test_parses_without_a_z_suffix(abacus_time):
    assert abacus_time.parse_iso("2026-08-05T21:00:00") == abacus_time.parse_iso("2026-08-05T21:00:00Z")


def test_timestamps_are_read_as_utc_not_local_time(abacus_time):
    """A local-time reading would skew every duration by the machine's offset —
    and the sign would flip when the developer travels."""
    import calendar

    expected = calendar.timegm((2026, 8, 5, 21, 0, 0, 0, 0, 0))
    assert abacus_time.parse_iso("2026-08-05T21:00:00Z") == expected


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-08-05", 12345, {}])
def test_unparsable_input_returns_none_rather_than_raising(abacus_time, bad):
    assert abacus_time.parse_iso(bad) is None


def test_minutes_between_two_stamps(abacus_time):
    assert abacus_time.minutes_between("2026-08-05T21:00:00Z", "2026-08-05T21:42:00Z") == 42


def test_minutes_between_rounds_to_the_nearest_minute(abacus_time):
    assert abacus_time.minutes_between("2026-08-05T21:00:00Z", "2026-08-05T21:00:40Z") == 1


def test_minutes_between_spans_midnight(abacus_time):
    assert abacus_time.minutes_between("2026-08-05T23:50:00Z", "2026-08-06T00:10:00Z") == 20


def test_a_negative_span_is_clamped_to_zero(abacus_time):
    """Clock skew or a resumed session must not produce a negative duration."""
    assert abacus_time.minutes_between("2026-08-05T21:42:00Z", "2026-08-05T21:00:00Z") == 0


@pytest.mark.parametrize("start,end", [
    (None, "2026-08-05T21:00:00Z"),
    ("2026-08-05T21:00:00Z", None),
    ("garbage", "2026-08-05T21:00:00Z"),
])
def test_an_unparsable_endpoint_yields_zero_minutes(abacus_time, start, end):
    assert abacus_time.minutes_between(start, end) == 0
