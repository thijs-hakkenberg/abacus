#!/usr/bin/env python3
"""Timestamp helpers. Pure stdlib, Python 3.9-compatible.

Everything in this plugin stamps and compares times in UTC ISO-8601 with a
trailing ``Z``, because that is the format Claude Code's OTEL exporter emits and
the format beads metadata reads back cleanly.

Parsing is hand-rolled rather than ``datetime.fromisoformat`` because Python
3.9's version rejects the ``Z`` suffix — the exact strings this plugin has to
read are the ones it cannot parse. (3.11 fixed this; the system interpreter here
is 3.9.6, see adr/006.)

``now_iso()`` honours ``$ABACUS_NOW`` so tests can assert an exact duration instead
of asserting a tolerance around wall-clock.
"""

import calendar
import os
import time


def now_iso():
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``, or ``$ABACUS_NOW`` if set."""
    override = os.environ.get("ABACUS_NOW")
    if override:
        return override
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def parse_iso(text):
    """ISO-8601 with an optional trailing Z -> epoch seconds, or None."""
    if not text:
        return None
    raw = str(text).strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    frac = 0.0
    if "." in raw:
        raw, _, frac_text = raw.partition(".")
        try:
            frac = float("0." + "".join(ch for ch in frac_text if ch.isdigit()))
        except ValueError:
            frac = 0.0
    try:
        parsed = time.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None
    return calendar.timegm(parsed) + frac


def iso_from_epoch(epoch):
    """Epoch seconds -> ``YYYY-MM-DDTHH:MM:SSZ``, or None.

    The inverse of :func:`parse_iso`, and the only one — git reports ``%ct``
    seconds and commit edges store an epoch, so two places need this conversion
    and neither should own its own copy of it.
    """
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def minutes_between(start_iso, end_iso):
    """Whole minutes from `start_iso` to `end_iso`; 0 if either is unparsable."""
    start = parse_iso(start_iso)
    end = parse_iso(end_iso)
    if start is None or end is None:
        return 0
    return max(0, int(round((end - start) / 60.0)))
