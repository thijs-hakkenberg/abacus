"""RED: automatic `bd init` for a project that has none.

Enforcement is only universal if every project has somewhere to claim a task.
With `gate.non_beads_project: "block"`, a directory with no workspace denies
Edit/Write — so without this, "enforce everywhere" means "hand-initialise every
repo you ever open", and the first thing the user does on hitting the deny is
reach for the escape hatch.

This hook creates a filesystem artefact in a directory the user may not own,
which makes the *rails* the substance of these tests rather than the happy path:

- off unless explicitly enabled, because a marketplace plugin must not silently
  write into every repo on the machine;
- git repositories only — a git root is a deliberate project boundary, and
  `--stealth` needs `.git/info/exclude` to keep `.beads/` uncommittable;
- never `$HOME`, never the filesystem root, and only inside configured roots;
- never on top of an existing workspace;
- and a `bd init` that reports success but leaves a database bd cannot read is
  treated as a failure, because that is the embedded-dolt failure mode and it
  only ever surfaces on the first read.

Everything here still exits 0. A hook that cannot create a workspace must leave
the session exactly as it found it (adr/002).
"""

import pytest

from conftest import session_payload

AUTO = {"auto_init": {"enabled": True, "roots": []}}


def _start(harness, payload=None, **kw):
    return harness.run_hook("session_start.py", payload or session_payload(), **kw)


def _init_calls(harness):
    return [c for c in harness.bd_calls() if c.startswith("bd init")]


def _context(result):
    return ((result.json or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_git_project_without_beads_is_initialised(harness):
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    res = _start(harness)
    assert res.rc == 0
    assert _init_calls(harness), "a new project should get a workspace"


def test_the_workspace_is_created_stealthily(harness):
    """`.beads/` must land in .git/info/exclude. Without --stealth the very next
    `git status` in a shared repo shows an untracked directory the user did not
    create and cannot explain to a reviewer."""
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    _start(harness)
    assert "--stealth" in _init_calls(harness)[0]


def test_the_init_cannot_prompt(harness):
    """`bd init` asks for a role when it can think a human is present. A prompt
    here blocks until SessionStart's 20s timeout and starts every session with a
    hung hook."""
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    _start(harness)
    assert "--non-interactive" in _init_calls(harness)[0]


def test_the_primer_follows_a_successful_init(harness):
    """The point of initialising is that the agent can then claim something; it
    has to be told so in the same turn, or it discovers the gate by being denied."""
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    assert "--claim" in _context(_start(harness))


# ── the rails ───────────────────────────────────────────────────────────────

def test_auto_init_is_off_by_default(harness):
    """A plugin installed user-wide must not write into repos unasked."""
    harness.make_git_project()
    harness.set_bd_json("list", [])
    assert _init_calls(harness) == []
    assert _start(harness).rc == 0
    assert _init_calls(harness) == []


def test_a_directory_that_is_not_a_git_repo_is_never_initialised(harness):
    """A scratch dir, /tmp, a downloads folder: no project boundary, no workspace."""
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    res = _start(harness)
    assert res.rc == 0
    assert _init_calls(harness) == []


def test_the_home_directory_is_never_initialised(harness):
    """A dotfiles repo at $HOME would otherwise capture every session that opens
    anywhere the walk-up finds it, gating unrelated work under one workspace."""
    harness.make_git_project(harness.home)
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    _start(harness, session_payload(cwd=str(harness.home)), cwd=harness.home)
    assert _init_calls(harness) == []


def test_a_project_outside_the_configured_roots_is_not_initialised(harness):
    harness.make_git_project()
    harness.write_config({"auto_init": {"enabled": True,
                                        "roots": [str(harness.tmp / "elsewhere")]}})
    harness.set_bd_json("list", [])
    _start(harness)
    assert _init_calls(harness) == []


def test_a_project_inside_a_configured_root_is_initialised(harness):
    harness.make_git_project()
    harness.write_config({"auto_init": {"enabled": True, "roots": [str(harness.tmp)]}})
    harness.set_bd_json("list", [])
    _start(harness)
    assert _init_calls(harness)


def test_a_sibling_of_a_configured_root_is_not_mistaken_for_a_child(harness):
    """String-prefix containment would read `/a/projects-old` as inside
    `/a/projects`."""
    root = harness.tmp / "projects"
    sibling = harness.tmp / "projects-old" / "repo"
    harness.make_git_project(sibling)
    harness.write_config({"auto_init": {"enabled": True, "roots": [str(root)]}})
    harness.set_bd_json("list", [])
    _start(harness, session_payload(cwd=str(sibling)), cwd=sibling)
    assert _init_calls(harness) == []


def test_an_existing_workspace_is_never_re_initialised(harness):
    harness.make_beads_project()
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    _start(harness)
    assert _init_calls(harness) == []


def test_a_subdirectory_of_an_initialised_repo_is_not_initialised(harness):
    """`.beads/` lives at the repo root; the hook's cwd is often deeper. A second
    workspace nested inside the first would split one project's tasks in two."""
    harness.make_beads_project()
    nested = harness.project / "src" / "deep"
    harness.make_git_project(nested)
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    _start(harness, session_payload(cwd=str(nested)), cwd=nested)
    assert _init_calls(harness) == []


def test_precompact_never_initialises(harness):
    """Compaction is the middle of a session, not the start of a project."""
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    res = _start(harness, session_payload(event="PreCompact"), extra_args=("--precompact",))
    assert res.rc == 0
    assert _init_calls(harness) == []


def test_the_kill_switch_disables_auto_init(harness):
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd_json("list", [])
    _start(harness, ABACUS_DISABLE="1")
    assert _init_calls(harness) == []


# ── failure is silent ───────────────────────────────────────────────────────

def test_a_failed_init_leaves_the_session_untouched(harness):
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd("init", rc=1)
    res = _start(harness)
    assert res.rc == 0
    assert _context(res) == "", "no workspace, nothing to say about tracking"


def test_an_init_that_cannot_be_read_back_is_not_a_workspace(harness):
    """The embedded-dolt failure mode: `bd init` exits 0 and the first read fails.
    Priming on that would advertise a gate over a database that does not resolve."""
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.set_bd("list", stdout="", rc=1)
    res = _start(harness)
    assert res.rc == 0
    assert _context(res) == ""


def test_bd_missing_entirely_is_not_an_error(harness):
    harness.make_git_project()
    harness.write_config(AUTO)
    harness.remove_bd()
    res = _start(harness)
    assert res.rc == 0
    assert res.stderr == "" or "Traceback" not in res.stderr


@pytest.mark.parametrize("roots", ["not-a-list", 42, {"a": 1}])
def test_a_malformed_roots_value_initialises_nothing(harness, roots):
    """The plugin's rule elsewhere is "fail open", but open here means *writing to
    the filesystem*. An unreadable scope is no scope, so a malformed roots value
    narrows to nothing rather than widening to every git repo on the machine."""
    harness.make_git_project()
    harness.write_config({"auto_init": {"enabled": True, "roots": roots}})
    harness.set_bd_json("list", [])
    res = _start(harness)
    assert res.rc == 0
    assert "Traceback" not in res.stderr
    assert _init_calls(harness) == []


def test_the_default_roots_do_not_cover_an_arbitrary_directory(harness):
    """`roots` omitted entirely is not the same as `roots: []`. Enabling auto-init
    without naming a scope must not turn every clone on the machine into a target."""
    harness.make_git_project()
    harness.write_config({"auto_init": {"enabled": True}})
    harness.set_bd_json("list", [])
    _start(harness)
    assert _init_calls(harness) == []
