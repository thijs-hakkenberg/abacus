#!/usr/bin/env python3
"""Configuration for abacus. Pure stdlib, Python 3.9-compatible.

Named ``abacus_config`` rather than ``config`` on purpose: hook scripts put
``hooks/lib`` on ``sys.path``, and a module named ``config`` there is a name
likely to collide with something else on the path in a plugin host that loads
several plugins' libraries.

Every knob lives in ``$ABACUS_STATE_DIR/config.json`` (default
``~/.claude/abacus/config.json``), and every value has a safe
default, so a missing or malformed config file can never break a session.
"""

import json
import os

# ── Pinned ccusage version ───────────────────────────────────────────────────
# Pin, never @latest: ccusage carries the pricing table, so floating the version
# silently re-prices historical tasks and makes two runs of the same report
# disagree. Upgrade path is deliberate — bump this, re-check a known session's
# total against the previous version, then commit the bump. See adr/003.
PINNED_CCUSAGE = "ccusage@20.0.14"

# 'calculate' = always price from tokens x rates, so figures stay consistent
# across periods regardless of what any individual transcript line recorded.
DEFAULT_MODE = "calculate"

_TRUTHY = {"1", "true", "yes", "on"}


def _defaults():
    return {
        "ccusage_version": PINNED_CCUSAGE,
        "ccusage_mode": DEFAULT_MODE,
        "ccusage_offline": False,
        # ccusage runs only at task boundaries, never on the edit hot path, but
        # an npx cold start can still take seconds — bound it so a slow or
        # wedged npx degrades to "no cost recorded" instead of eating the
        # hook's whole timeout budget.
        "ccusage_timeout_s": 25,
        "cache_ttl_s": 30,
        "gate": {
            "enabled": True,
            # What to do in a directory with no beads workspace at all. Default
            # 'warn' rather than 'block': a plugin installed user-wide must not
            # make unrelated repos un-editable.
            "non_beads_project": "warn",
        },
        # Create a beads workspace in a project that has none, so that
        # gate.non_beads_project="block" has a remedy already in place rather
        # than a deny to recover from.
        "auto_init": {
            # Off by default, and this one is not a preference. Every other
            # default here only decides what the plugin *says*; this one writes a
            # directory into a repository the user may not own, so it has to be
            # asked for.
            "enabled": False,
            # Restrict to these roots (``~`` expanded). An explicit empty list
            # means "any git repository", which is the broad reading and has to
            # be chosen rather than fallen into — hence a narrow default rather
            # than [].
            "roots": [os.path.join("~", "projects")],
            # --stealth writes .beads to .git/info/exclude. Off only if you want
            # the workspace committed and shared with the repo.
            "stealth": True,
        },
        "prime": {"enabled": True},
        # 'push' = `bd dolt push` at session end, 'sync', or 'off'.
        "sync_on_session_end": "off",
        "statusline": True,
        "otel_enrichment": True,
        "otel_events_path": os.path.join("~", ".claude", "logs", "claude-code-events.jsonl"),
        "state_max_age_days": 14,
    }


def config_path():
    from state_store import state_dir

    return os.path.join(state_dir(), "config.json")


# Where this plugin's config lived when the plugin was called task-cost-tracker.
# State is disposable and is not migrated, but config is a user's stated intent:
# someone who set ``gate.non_beads_project: "block"`` and then upgraded would
# otherwise fall back to ``warn`` with no message — an enforcement regression that
# looks exactly like the plugin working.
LEGACY_CONFIG_PATH = os.path.join("~", ".claude", "task-cost-tracker", "config.json")


def legacy_config_path():
    """The pre-rename config location, or None when an explicit state dir is set.

    ``ABACUS_STATE_DIR`` means "look exactly here", so it suppresses the search
    rather than widening it.
    """
    if (os.environ.get("ABACUS_STATE_DIR") or "").strip():
        return None
    return os.path.expanduser(LEGACY_CONFIG_PATH)


def _deep_merge(base, overlay):
    """Merge `overlay` into `base`, recursing into nested dicts.

    A plain ``dict.update`` would let ``{"gate": {"non_beads_project": "off"}}``
    delete the sibling ``gate.enabled`` default, silently turning the gate's
    own kill switch into a missing key.
    """
    for key, value in overlay.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None):
    """Return the merged config. Missing/malformed file → defaults only.

    An explicit `path` is taken literally — naming a file that does not exist
    means defaults, not a search. Only the default location falls back to the
    pre-rename one, and only when it has nothing of its own.
    """
    cfg = _defaults()
    try:
        if path is None:
            path = config_path()
            if not os.path.isfile(path):
                path = legacy_config_path() or path
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                _deep_merge(cfg, user)
    except Exception:
        pass  # a broken config never breaks a session — defaults stand
    return cfg


def is_disabled():
    """True when the user has switched the plugin off entirely.

    Two mechanisms, because they serve different needs: the env var is per-shell
    and scriptable (the documented escape hatch when the gate is in the way),
    the marker file is durable across sessions.
    """
    if os.environ.get("ABACUS_DISABLE", "").strip().lower() in _TRUTHY:
        return True
    try:
        from state_store import state_dir

        return os.path.exists(os.path.join(state_dir(), "disabled"))
    except Exception:
        return False


def gate_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool((cfg.get("gate") or {}).get("enabled", True))


def _auto_init(cfg):
    block = (cfg if cfg is not None else load_config()).get("auto_init")
    return block if isinstance(block, dict) else {}


def auto_init_enabled(cfg=None):
    return bool(_auto_init(cfg).get("enabled", False))


def auto_init_stealth(cfg=None):
    return bool(_auto_init(cfg).get("stealth", True))


def auto_init_roots(cfg=None):
    """Directories under which auto-init may act.

    ``[]`` means every git repository. ``None`` means the configured value could
    not be read as a list of paths — deliberately distinct, because the fallback
    for "I cannot tell what scope you meant" must be *no* scope. Everywhere else
    in this plugin an unreadable value degrades towards doing nothing visible;
    here doing nothing visible is exactly what narrowing achieves.
    """
    roots = _auto_init(cfg).get("roots", None)
    if roots is None:
        return None
    if isinstance(roots, (list, tuple)):
        return [str(r) for r in roots if isinstance(r, str) and r.strip()]
    return None


def non_beads_mode(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    mode = (cfg.get("gate") or {}).get("non_beads_project", "warn")
    return mode if mode in ("warn", "off", "block") else "warn"
