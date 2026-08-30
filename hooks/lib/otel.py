#!/usr/bin/env python3
"""OTEL event-log reader — supplementary duration/activity texture.

ccusage answers "what did this task cost"; it cannot answer "how long was the
agent actually working" or "how many tools did it run". Those come from the OTLP
file exporter the user already has configured, which lands OTLP-JSON lines in
``~/.claude/logs/claude-code-events.jsonl``.

This is strictly best-effort (adr/003): OTEL might be disabled, the collector
might be down, the file might be mid-write. Every failure returns ``{}`` and the
caller simply omits those metadata keys. Nothing here is required for a correct
cost figure.

Attribute names verified against a live collector log on 2026-08-05, Claude Code
2.1.220. Two things worth knowing before editing this file:

- The session attribute is ``session.id`` (dotted), *not* ``session_id`` as the
  hook payload spells it. Getting this wrong yields a silent zero.
- ``api_request`` events carry a ``cost_usd`` attribute. It is deliberately NOT
  used: ccusage is the single cost authority with a pinned pricing table and
  subagent-aware deduplication, and having two cost paths that can disagree is
  worse than having one. OTEL contributes counts and durations only.

The log is tens of megabytes and append-only, so reads are tail-windowed rather
than whole-file.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_TAIL_BYTES = 4 * 1024 * 1024


import abacus_time  # noqa: E402

_parse_ts = abacus_time.parse_iso


def _iter_records(path, tail_bytes):
    """Yield (attributes dict) for each log record in the tail of `path`."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            fh.readline()  # discard the partial first line
            scanned = tail_bytes
        else:
            scanned = size
        for raw in fh:
            try:
                doc = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, TypeError):
                continue
            if not isinstance(doc, dict):
                continue
            for resource in doc.get("resourceLogs") or []:
                for scope in (resource or {}).get("scopeLogs") or []:
                    for record in (scope or {}).get("logRecords") or []:
                        attrs = {}
                        for attr in (record or {}).get("attributes") or []:
                            key = attr.get("key")
                            value = attr.get("value")
                            if key is None or not isinstance(value, dict):
                                continue
                            # OTLP wraps scalars as {stringValue|intValue|doubleValue: x}
                            for candidate in value.values():
                                attrs[key] = candidate
                                break
                        if attrs:
                            yield attrs, scanned


def _as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def window_stats(session_id, start_iso, end_iso=None, path=None, tail_bytes=DEFAULT_TAIL_BYTES):
    """Activity stats for one session between two timestamps.

    Returns ``{tool_calls, tool_ms, api_calls, api_ms, active_min, models,
    bytes_scanned}`` or ``{}`` when the log is unavailable or the window is
    unparsable. ``active_min`` sums tool and API durations, which measures time
    the agent spent doing something rather than wall-clock elapsed — the two
    differ a lot when a task sits open while the user reads.
    """
    start = _parse_ts(start_iso)
    if start is None:
        return {}
    end = _parse_ts(end_iso) if end_iso else None

    if path is None:
        try:
            import abacus_config

            path = abacus_config.load_config().get("otel_events_path")
        except Exception:
            return {}
    path = os.path.expanduser(str(path or ""))
    if not path or not os.path.isfile(path):
        return {}

    stats = {"tool_calls": 0, "tool_ms": 0, "api_calls": 0, "api_ms": 0, "bytes_scanned": 0}
    models = set()
    try:
        for attrs, scanned in _iter_records(path, tail_bytes):
            stats["bytes_scanned"] = scanned
            if str(attrs.get("session.id") or "") != str(session_id):
                continue
            when = _parse_ts(attrs.get("event.timestamp"))
            if when is None or when < start:
                continue
            if end is not None and when > end:
                continue
            event = str(attrs.get("event.name") or "")
            if event == "tool_result":
                stats["tool_calls"] += 1
                stats["tool_ms"] += _as_int(attrs.get("duration_ms"))
            elif event == "api_request":
                stats["api_calls"] += 1
                stats["api_ms"] += _as_int(attrs.get("duration_ms"))
                model = attrs.get("model")
                if model:
                    models.add(str(model))
    except OSError:
        return {}

    stats["models"] = sorted(models)
    stats["active_min"] = int(round((stats["tool_ms"] + stats["api_ms"]) / 60000.0))
    return stats
