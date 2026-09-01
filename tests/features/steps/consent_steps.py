"""Steps for ``consent-acknowledgement.feature`` — the one precondition on everything.

Kept in its own module because its vocabulary is about a posture rather than a
behaviour: whether abacus has been *agreed to*, not what it does once it has.
Every other feature runs in the acknowledged steady state, which is why the
harness acknowledges by default; these scenarios are the only ones that undo it.

Two habits worth naming:

- ``auto_acknowledge`` is turned off by any Given that touches consent, so a
  later config write cannot silently restore an agreement the scenario just
  withdrew. Without it, ``merge_config`` re-acknowledges and a scenario about
  widened scope would assert against a posture it never reached.
- The Thens assert on *what the notice tells the user*, not on its wording —
  that abacus is governing nothing, that the actual configured root is named, and
  that the command to agree is there. Rewording the notice is a patch bump in
  ``contracts/output/consent-notice.md``, and a feature file that pinned the
  prose would turn every rewording into a failing test.
"""

import json

from pytest_bdd import given, then, when

from conftest import session_payload
from when_steps import step_run, step_run_statusline
from world import injected_context, merge_config, world  # noqa: F401


def _run_acknowledge(world, extra_args=()):
    """Drive the consent surface as a subprocess, like the command file does."""
    return step_run(world, "acknowledge.py",
                    session_payload(session_id=world["session"], cwd=world["cwd"]),
                    extra_args=extra_args)


# ══ Given: the consent posture ══════════════════════════════════════════════

@given("abacus has not been acknowledged")
def step_not_acknowledged(world):
    world["auto_acknowledge"] = False
    world["harness"].revoke_acknowledgement()


@given("the governing settings have been acknowledged")
def step_acknowledged(world):
    # Records consent for whatever is configured *now*, which is why scenarios
    # about a changed setting put this step between the two configurations.
    world["auto_acknowledge"] = False
    world["harness"].acknowledge()


@given("the pinned ccusage version is changed")
def step_ccusage_version_changed(world):
    # The canonical cosmetic change: it alters the pricing table a figure is
    # derived from, never whether abacus denies, writes, or reaches a remote.
    merge_config(world, {"ccusage_version": "ccusage@99.0.0"})


@given("the state directory cannot be written to")
def step_state_dir_unwritable(world):
    # A *file* where the directory should be, so `makedirs` fails outright.
    # Chmod would not do it: the suite may run as root in CI, where a 0500
    # directory is still writable.
    blocked = world["harness"].tmp / "not-a-directory"
    blocked.write_text("")
    world["env"]["ABACUS_STATE_DIR"] = str(blocked)


# ══ When: the consent surface ═══════════════════════════════════════════════

@when("the UserPromptSubmit hook runs again")
def step_prompt_again(world):
    step_run_statusline(world)


@when("the agreement is recorded")
def step_accept(world):
    _run_acknowledge(world, extra_args=("--accept",))


@when("the agreement is withdrawn")
def step_revoke(world):
    _run_acknowledge(world, extra_args=("--revoke",))


@when("the notice is shown without an answer")
def step_show(world):
    # No flags at all — the default has to be the reading, not the agreeing.
    _run_acknowledge(world)


# ══ Then: what the notice says ══════════════════════════════════════════════

@then("the injected context says abacus is governing nothing")
def step_context_says_inert(world):
    context = injected_context(world)
    assert "not governing anything" in context, (
        "the first thing the notice has to establish is that nothing is being "
        "enforced yet: %r" % context)


@then("the injected context lists what agreeing would switch on")
def step_context_lists_effects(world):
    context = injected_context(world)
    # The gate and the metadata write are enabled by default, so both belong in
    # the list on a stock install. Named as effects, not as settings: a user
    # deciding whether to agree is reading for consequences.
    assert "deny Edit/Write" in context, "the notice must name the denial: %r" % context
    assert "abacus_* cost metadata" in context, (
        "the notice must name the write to the issue: %r" % context)


@then("the injected context names the command that records agreement")
def step_context_names_command(world):
    assert "/abacus:acknowledge" in injected_context(world)


@then("the injected context names the directory a workspace could be created in")
def step_context_names_root(world):
    root = world["config"]["auto_init"]["roots"][0]
    assert root in injected_context(world), (
        "a generic notice is not informed consent; it has to name the actual "
        "configured root %r" % root)


@then("the injected context says a remote would be reached")
def step_context_names_push(world):
    context = injected_context(world)
    assert "bd dolt push" in context and "remote" in context, (
        "reaching a remote is the one effect the user cannot inspect afterwards, "
        "so the notice must say so explicitly: %r" % context)


@then("the notice names the setting that changed")
def step_notice_names_changed_setting(world):
    stdout = world["result"].stdout
    assert "auto_init.roots" in stdout, (
        "a re-ask that does not say what moved is indistinguishable from a bug: "
        "%r" % stdout)


@then("abacus is still not acknowledged")
def step_still_not_acknowledged(world):
    # Asked of the tool itself, in the same broken environment, because the
    # record is wherever that environment says it is. A failed write that
    # reported success would be the one bug this whole feature cannot survive.
    result = _run_acknowledge(world, extra_args=("--json",))
    assert json.loads(result.stdout)["acknowledged"] is False
