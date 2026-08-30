"""Step definitions for workspace-auto-init.feature.

Kept in their own module rather than added to ``common_steps`` because none of
these sentences appear in any other feature: auto-init is the one behaviour that
creates a filesystem artefact, and its vocabulary (git repository, configured
root, excluded from version control) belongs to it alone.
"""

from pytest_bdd import given, then

from world import merge_config, world  # noqa: F401 — fixture must be importable


def _init_calls(world):
    return [c for c in world["harness"].bd_calls() if c.startswith("bd init")]


# ══ Given ═══════════════════════════════════════════════════════════════════

@given("the working directory is a git repository")
def step_cwd_is_git_repo(world):
    world["harness"].make_git_project(world["cwd"])


@given("the working directory is not a git repository")
def step_cwd_is_not_git_repo(world):
    # A fresh directory rather than removing the marker: this Given follows the
    # Background one, and a scenario should never depend on undoing a step.
    plain = world["harness"].tmp / "no-git"
    plain.mkdir(exist_ok=True)
    world["cwd"] = plain


@given("automatic workspace initialisation is enabled for the whole machine")
def step_auto_init_everywhere(world):
    # An explicit empty roots list is the "any git repository" reading — it has
    # to be written out, because omitting roots leaves the narrow default.
    merge_config(world, {"auto_init": {"enabled": True, "roots": []}})


@given("automatic workspace initialisation is enabled only under a different root")
def step_auto_init_elsewhere(world):
    merge_config(world, {"auto_init": {
        "enabled": True,
        "roots": [str(world["harness"].tmp / "some-other-root")],
    }})


@given("the bd init command exits non-zero")
def step_bd_init_fails(world):
    world["harness"].set_bd("init", stdout="Error: could not create database", rc=1)


# ══ Then ════════════════════════════════════════════════════════════════════

@then("a beads workspace is created")
def step_workspace_created(world):
    assert _init_calls(world), "expected bd init to have been invoked"


@then("no beads workspace is created")
def step_no_workspace_created(world):
    assert _init_calls(world) == [], "bd init ran in a directory it should not touch"


@then("the workspace is created without a prompt")
def step_init_is_non_interactive(world):
    # bd asks for an actor role when it thinks a human is present; a prompt here
    # hangs the hook until SessionStart's timeout expires.
    assert "--non-interactive" in _init_calls(world)[0]


@then("the workspace is excluded from version control")
def step_init_is_stealthy(world):
    # --stealth writes .beads to .git/info/exclude, so a workspace this plugin
    # created unprompted can never appear in someone's commit.
    assert "--stealth" in _init_calls(world)[0]
