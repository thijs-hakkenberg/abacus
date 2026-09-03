"""Enforces this repo's own specification layout. There is no external authority.

The layout — four pillars plus a machine-readable manifest — is self-imposed
(adr/007), so this module is the only thing that enforces it. That is worth
stating plainly, because it bounds what a pass means: these assertions check that
the artefacts **agree with each other**, not that they still describe the
software. Nothing here can tell you a feature file has gone stale, which is the
failure most likely to happen and the one a human reviewer has to catch.

What it does check is the class of error a reviewer is least likely to catch by
reading, because it lives in the gaps between two files:

- the four pillars exist and are non-empty
- `spec.manifest.yaml` parses and every path under `artefacts:` resolves
- every hook script the manifest names exists on disk
- every manifest `sla.latency_p99_ms` equals its `hooks.json` timeout x 1000
- the canvas carries all eleven Bounded Context Canvas H2 sections, in order
- every `.feature` parses, and is actually collected by the suite
- **every scenario step resolves to a step definition** — an unbound scenario must
  fail, not skip, or the feature space is documentation pretending to be a test
"""

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS_DIR = REPO_ROOT / "tests" / "features" / "steps"

yaml = pytest.importorskip(
    "yaml", reason="pyyaml is a test-only dependency; hooks stay stdlib-only (adr/006)"
)


# ── Pillar 0: the manifest ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def manifest():
    path = REPO_ROOT / "spec.manifest.yaml"
    assert path.is_file(), "spec.manifest.yaml belongs at the repo root, not under contracts/"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_manifest_declares_the_ea_graph_node_fields(manifest):
    for key in (
        "name",
        "description",
        "type",
        "ea_layer",
        "owner",
        "status",
        "business_capability",
        "artefacts",
        "interfaces",
        "dependencies",
    ):
        assert manifest.get(key), "spec.manifest.yaml is missing or has an empty %r" % key


def test_every_artefact_path_in_the_manifest_resolves(manifest):
    # British spelling `artefacts` is deliberate and load-bearing (adr/007).
    for pillar, rel in manifest["artefacts"].items():
        target = REPO_ROOT / rel
        assert target.is_dir(), "artefacts.%s points at %s, which is not a directory" % (
            pillar,
            rel,
        )
        assert any(target.iterdir()), "artefacts.%s (%s) is empty" % (pillar, rel)


def test_every_dependency_points_at_a_readable_contract(manifest):
    for dep in manifest["dependencies"]:
        assert dep.get("name"), "a dependency entry has no name"
        target = REPO_ROOT / dep["contract"]
        assert target.is_file(), "dependency %r cites %s, which does not exist" % (
            dep["name"],
            dep["contract"],
        )


# ── Pillar 0 vs the wiring: the manifest must not drift from hooks.json ──────

# Each hook event, the manifest interface that must describe it, and the script
# hooks.json must invoke. Written out rather than derived, so that adding an event
# to hooks.json without declaring it fails here instead of silently passing.
EVENT_TO_INTERFACE = {
    "SessionStart": ("session-start-hook", "session_start.py"),
    "PreToolUse": ("pre-tool-use-gate", "gate_edits.py"),
    "PostToolUse": ("post-tool-use-bash-watcher", "watch_bd_commands.py"),
    "UserPromptSubmit": ("user-prompt-submit-hook", "prompt_statusline.py"),
    "Stop": ("stop-hook", "stop_reconcile.py"),
    "PreCompact": ("pre-compact-hook", "session_start.py"),
    "SessionEnd": ("session-end-hook", "session_end.py"),
}


@pytest.fixture(scope="module")
def hook_timeouts():
    import json

    with (REPO_ROOT / "hooks" / "hooks.json").open(encoding="utf-8") as fh:
        wiring = json.load(fh)["hooks"]
    return {
        event: entries[0]["hooks"][0]["timeout"] for event, entries in wiring.items()
    }


def test_manifest_declares_one_inbound_interface_per_hook_event(manifest, hook_timeouts):
    declared = {i["name"] for i in manifest["interfaces"]["inbound"]}
    expected = {name for name, _ in EVENT_TO_INTERFACE.values()}

    assert set(hook_timeouts) == set(EVENT_TO_INTERFACE), (
        "hooks.json wires %s but this test knows about %s — update both"
        % (sorted(hook_timeouts), sorted(EVENT_TO_INTERFACE))
    )
    assert declared == expected, "manifest inbound interfaces drifted from hooks.json"


@pytest.mark.parametrize("event", sorted(EVENT_TO_INTERFACE))
def test_inbound_sla_mirrors_the_hook_timeout(event, manifest, hook_timeouts):
    """A timeout is a promise about worst-case latency; the SLA must repeat it.

    These two numbers are in different files with no mechanism keeping them
    together, which is exactly why the check exists.
    """
    iface_name, _ = EVENT_TO_INTERFACE[event]
    iface = next(i for i in manifest["interfaces"]["inbound"] if i["name"] == iface_name)

    assert iface["sla"]["latency_p99_ms"] == hook_timeouts[event] * 1000, (
        "%s declares latency_p99_ms=%s but hooks.json gives %s a %ss timeout"
        % (iface_name, iface["sla"]["latency_p99_ms"], event, hook_timeouts[event])
    )


@pytest.mark.parametrize("event", sorted(EVENT_TO_INTERFACE))
def test_the_script_each_inbound_interface_names_exists(event, manifest):
    """adr/007 commits this test to checking that declared scripts are real.

    A manifest naming a script that was renamed or deleted is worse than one that
    names none: it reads as verified.
    """
    iface_name, script = EVENT_TO_INTERFACE[event]
    iface = next(i for i in manifest["interfaces"]["inbound"] if i["name"] == iface_name)

    named = re.findall(r"hooks/scripts/([\w]+\.py)", iface["schema"])
    assert named, "%s names no hook script in its schema field" % iface_name
    assert script in named, "%s should name %s, names %s" % (iface_name, script, named)

    for candidate in named:
        assert (
            REPO_ROOT / "hooks" / "scripts" / candidate
        ).is_file(), "%s names %s, which does not exist" % (iface_name, candidate)


def test_every_contract_the_manifest_cites_exists(manifest):
    cited = set()
    for direction in ("inbound", "outbound"):
        for iface in manifest["interfaces"][direction]:
            cited.update(re.findall(r"contracts/\w+/[\w.-]+\.md", iface["schema"]))

    assert cited, "no interface cites a contract file"
    for rel in sorted(cited):
        assert (REPO_ROOT / rel).is_file(), "manifest cites %s, which does not exist" % rel


# ── Pillar 1: ADRs ──────────────────────────────────────────────────────────

ADR_FILENAME = re.compile(r"^\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ADR_SECTIONS = ("## Status", "## Date", "## Context", "## Decision", "## Consequences")


def _adrs():
    return sorted((REPO_ROOT / "adr").glob("*.md"))


def test_the_adr_pillar_is_populated():
    assert len(_adrs()) >= 7, "the plan committed to at least seven ADRs"


@pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.name)
def test_adr_filename_and_sections(path):
    assert ADR_FILENAME.match(path.name), (
        "%s must be NNN-lowercase-title.md with no 'ADR-' filename prefix "
        "(naming convention, adr/007)" % path.name
    )
    text = path.read_text(encoding="utf-8")
    for section in ADR_SECTIONS:
        assert section in text, "%s has no %r section (Nygard shape)" % (path.name, section)
    assert (
        "## Alternatives Considered" in text
    ), "%s records no alternatives; a decision with no rejected options is not a decision" % (
        path.name
    )


def test_adr_numbers_are_unique_and_gapless():
    numbers = sorted(int(p.name[:3]) for p in _adrs())
    assert numbers == list(range(1, len(numbers) + 1)), (
        "ADR numbers should run 001..%03d with no gaps or duplicates, got %s"
        % (len(numbers), numbers)
    )


# ── Pillar 1a: the analysis companions ──────────────────────────────────────
#
# `adr/analysis/NNN-<same-slug>.md` archives the weighing an ADR compresses away:
# the criteria stated before the options, the option that was eliminated and by
# which argument, the calibrated confidence, the premortem. adr/007's addendum
# records why it is a subdivision of the "why" pillar rather than a fifth one.
#
# The hazard the three assertions below exist for is mechanical: `_adrs()` globs
# `adr/*.md` non-recursively and the loose-markdown check globs the root only, so
# without these the companions would be *entirely unchecked* — which is the thing
# this repo calls documentation pretending to be a test.
#
# What they deliberately do not assert, in the spirit of adr/007's own admission:
# that an analysis is sound, or still true. One can be wholly superseded and the
# suite stays green. They check only that it is present, well-formed and reachable.

ANALYSIS_SECTIONS = (
    "## Frame",
    "## Criteria",
    "## Options",
    "## Assessment",
    "## Sensitivity and Trade-off Points",
    "## Evidence Certainty",
    "## Assumptions and Falsifiers",
    "## Decision and Warrant",
    "## Consequences",
    "## Y-statements",
)


def _analyses():
    return sorted((REPO_ROOT / "adr" / "analysis").glob("*.md"))


def test_the_analysis_companions_are_populated():
    """At least one, or the directory is a promise rather than a practice."""
    assert _analyses(), (
        "adr/analysis/ holds no companion; the weighing behind at least the most "
        "recent decision should be archived (adr/007 addendum)"
    )


@pytest.mark.parametrize("path", _analyses(), ids=lambda p: p.name)
def test_every_analysis_belongs_to_an_adr_that_exists(path):
    """One-directional on purpose.

    An ADR without a companion is legal — adr/001–014 predate the practice, and
    reconstructing their weighing now would be invention, which is the error
    adr/013 refuses. An analysis without an ADR is not legal: it is reasoning for
    a decision nobody can find.
    """
    assert ADR_FILENAME.match(path.name), (
        "%s must follow the same NNN-lowercase-title.md convention as the ADR it "
        "belongs to" % path.name
    )
    siblings = list((REPO_ROOT / "adr").glob("%s-*.md" % path.name[:3]))
    assert siblings, "adr/analysis/%s is an orphan: there is no adr/%s-*.md" % (
        path.name, path.name[:3],
    )
    assert path.name in {p.name for p in siblings}, (
        "adr/analysis/%s must share its slug with %s, so the pair is obvious from "
        "the filename alone" % (path.name, siblings[0].name)
    )


@pytest.mark.parametrize("path", _analyses(), ids=lambda p: p.name)
def test_every_analysis_carries_the_composite_scaffold(path):
    """The scaffold is the reusable part.

    Stated criteria before options is what makes the reasoning auditable rather
    than a rationalisation written after the fact; the premortem and the falsifiers
    are what make it reviewable when it turns out wrong.
    """
    text = path.read_text(encoding="utf-8")
    for section in ANALYSIS_SECTIONS:
        assert section in text, "%s has no %r section (Composite Scaffold)" % (
            path.name, section,
        )


@pytest.mark.parametrize("path", _analyses(), ids=lambda p: p.name)
def test_the_parent_adr_links_to_its_analysis(path):
    """Without this, a reader of the ADR never learns the reasoning exists.

    An unreachable archive is the same as no archive, and this is the assertion
    most likely to catch a real omission: writing the companion and forgetting the
    link is a far easier mistake than writing neither.
    """
    parent = REPO_ROOT / "adr" / path.name
    assert parent.is_file(), "no parent ADR at adr/%s" % path.name
    text = parent.read_text(encoding="utf-8")
    assert "analysis/%s" % path.name in text, (
        "adr/%s does not link to analysis/%s; the weighing is archived but "
        "unreachable from the decision it explains" % (path.name, path.name)
    )


# ── Pillar 3: the bounded-context canvas ────────────────────────────────────

# The ddd-crew Bounded Context Canvas sections, in its order. Extra sections are allowed —
# this repo adds one on the blocking/non-blocking split — but the eleven must all
# be present and must not be reordered, because a canvas is read top to bottom.
CANVAS_SECTIONS = (
    "Name",
    "Purpose",
    "Strategic Classification",
    "Domain Roles",
    "Inbound Communication",
    "Outbound Communication",
    "Ubiquitous Language",
    "Business Decisions",
    "Assumptions",
    "Verification Metrics",
    "Open Questions",
)


@pytest.fixture(scope="module")
def canvas_headings():
    path = REPO_ROOT / "contexts" / "abacus-canvas.md"
    assert path.is_file(), "the bounded-context canvas is missing"
    return [
        line[3:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]


def test_the_canvas_carries_every_required_section(canvas_headings):
    missing = [s for s in CANVAS_SECTIONS if s not in canvas_headings]
    assert not missing, "canvas is missing sections: %s" % missing


def test_the_canvas_sections_are_in_the_canonical_order(canvas_headings):
    positions = [canvas_headings.index(s) for s in CANVAS_SECTIONS]
    assert positions == sorted(positions), (
        "canvas sections are out of order: %s"
        % [canvas_headings[i] for i in sorted(positions)]
    )


def test_the_context_map_exists_and_names_the_subject_context():
    path = REPO_ROOT / "contexts" / "context-map.d2"
    assert path.is_file(), "context-map.d2 is missing — a canvas without a map has no boundaries"
    text = path.read_text(encoding="utf-8")
    assert "#8B5E34" in text, "the subject context should be filled distinctly from collaborators"
    assert "shape: person" in text, "humans in a context map are drawn as people"


# ── Pillar 4: contracts ─────────────────────────────────────────────────────

CONTRACT_SECTIONS = ("## MCP binding", "## SemVer", "## SLA + telemetry")


def _contracts():
    return sorted((REPO_ROOT / "contracts").glob("*/*.md"))


def test_both_contract_directions_are_populated():
    directions = {p.parent.name for p in _contracts()}
    assert directions == {"input", "output"}, "expected contracts/input and contracts/output"


@pytest.mark.parametrize("path", _contracts(), ids=lambda p: "%s/%s" % (p.parent.name, p.name))
def test_contract_declares_versioning_and_sla(path):
    text = path.read_text(encoding="utf-8")
    for section in CONTRACT_SECTIONS:
        assert section in text, "%s has no %r section" % (path.name, section)
    assert "latency_p99_ms" in text, "%s declares no latency SLA" % path.name


def test_every_hook_event_has_an_input_contract():
    present = {p.stem for p in (REPO_ROOT / "contracts" / "input").glob("*.md")}
    expected = {
        "session-start",
        "pre-tool-use",
        "post-tool-use-bash",
        "user-prompt-submit",
        "stop",
        "pre-compact",
        "session-end",
    }
    assert expected <= present, "missing input contracts: %s" % sorted(expected - present)


# ── Pillar 2: the feature space, and whether it is really executable ────────


def _feature_files():
    return sorted((REPO_ROOT / "features").glob("*.feature"))


@pytest.fixture(scope="module")
def parsed_features():
    """Parse every .feature with pytest-bdd's own parser, not a regex.

    Using the real parser is the point: a file this cannot parse is a file the
    suite cannot execute, so a parse failure here is the same failure a reviewer
    would otherwise discover at collection time.
    """
    parser = pytest.importorskip("pytest_bdd.parser")
    out = {}
    for path in _feature_files():
        out[path.name] = parser.FeatureParser("features", path.name).parse()
    return out


def test_the_feature_pillar_is_populated():
    assert len(_feature_files()) >= 6, "the plan committed to one feature file per behaviour"


@pytest.mark.parametrize("path", _feature_files(), ids=lambda p: p.name)
def test_feature_file_parses_and_has_scenarios(path, parsed_features):
    feature = parsed_features[path.name]
    assert feature.scenarios, "%s parses but declares no scenarios" % path.name


@pytest.mark.parametrize("path", _feature_files(), ids=lambda p: p.name)
def test_feature_file_is_actually_collected_by_the_suite(path):
    """An artefact nobody runs is prose. Every .feature must be bound in the suite.

    Without this, adding a seventh feature file would produce a document that
    looks like a test and never executes.
    """
    bindings = (REPO_ROOT / "tests" / "features" / "test_features.py").read_text(
        encoding="utf-8"
    )
    assert (
        'scenarios("%s")' % path.name in bindings
    ), "%s is never passed to scenarios(); it would never run" % path.name


@pytest.fixture(scope="module")
def step_parsers():
    """Every registered step definition, as (type, parser) pairs.

    pytest-bdd stores each definition's context on a marker function it injects
    into the defining module's namespace, so the registry is read from the step
    modules themselves rather than reconstructed.

    The modules are discovered rather than listed. A hardcoded list makes a new
    step module invisible here, so a feature bound in ``test_features.py`` and
    passing there would be reported as unbound by this test — a false failure that
    invites deleting the check instead of the cause.
    """
    modules = []
    sys.path.insert(0, str(STEPS_DIR))
    try:
        for path in sorted(STEPS_DIR.glob("*_steps.py")):
            modules.append(importlib.import_module(path.stem))
    finally:
        sys.path.remove(str(STEPS_DIR))
    assert modules, "no step modules found in %s" % STEPS_DIR

    parsers = []
    for module in modules:
        for name, obj in vars(module).items():
            if not name.startswith("pytestbdd_stepdef"):
                continue
            ctx = obj.__wrapped__().__self__ if hasattr(obj, "__self__") else None
            ctx = getattr(obj, "_pytest_bdd_step_context", ctx)
            if ctx is not None:
                parsers.append((ctx.type, ctx.parser))
    assert parsers, "no step definitions were discovered — the registry read is broken"
    return parsers


def _scenario_steps(feature):
    scenarios = (
        feature.scenarios.values()
        if isinstance(feature.scenarios, dict)
        else feature.scenarios
    )
    for scenario in scenarios:
        for step in scenario.all_background_steps + scenario.steps:
            yield scenario.name, step


@pytest.mark.parametrize("path", _feature_files(), ids=lambda p: p.name)
def test_every_scenario_step_has_a_binding(path, parsed_features, step_parsers):
    """adr/007 commits this test to failing when a scenario lacks a binding.

    pytest-bdd would raise StepDefinitionNotFoundError at run time, which is
    already a failure rather than a skip — this check finds it without running the
    scenario, and reports every unbound step at once instead of the first.
    """
    unbound = []
    for scenario_name, step in _scenario_steps(parsed_features[path.name]):
        matched = any(
            (kind is None or kind == step.type) and parser.is_matching(step.name)
            for kind, parser in step_parsers
        )
        if not matched:
            unbound.append("%s: %s %s" % (scenario_name, step.type, step.name))

    assert not unbound, "unbound steps in %s:\n  %s" % (path.name, "\n  ".join(unbound))


# ── Layout hygiene ──────────────────────────────────────────────────────────


def test_only_the_three_permitted_loose_markdown_files_exist_at_the_root():
    """The repo's rule: a design note goes in an ADR or a contract, never at the root.

    Left unchecked, top-level markdown is where undated, unowned design notes
    accumulate — which is precisely what the four pillars exist to prevent.
    """
    loose = {p.name for p in REPO_ROOT.glob("*.md")}
    assert loose <= {"README.md", "CLAUDE.md", "CHANGELOG.md"}, (
        "unexpected top-level markdown: %s — put it in adr/ or contracts/"
        % sorted(loose - {"README.md", "CLAUDE.md", "CHANGELOG.md"})
    )


def test_no_directory_is_named_mcp():
    """It collides with the installed MCP SDK — a hazard learned the hard way."""
    assert not (REPO_ROOT / "mcp").exists(), "a directory named mcp/ shadows the SDK package"


def test_the_plugin_and_marketplace_versions_agree():
    import json

    with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
        plugin = json.load(fh)
    with (REPO_ROOT / ".claude-plugin" / "marketplace.json").open(encoding="utf-8") as fh:
        market = json.load(fh)

    entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
    assert entry["version"] == plugin["version"], (
        "plugin.json is %s but marketplace.json advertises %s"
        % (plugin["version"], entry["version"])
    )


def test_the_changelog_documents_the_current_version():
    import json

    with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
        version = json.load(fh)["version"]
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "[%s]" % version in changelog, (
        "CHANGELOG.md has no entry for %s; Keep a Changelog is kept in step with "
        "plugin.json version bumps" % version
    )


# ── Publication: the repo is its own marketplace ─────────────────────────────
#
# This repo is added directly as a marketplace, so `marketplace.json` is not an
# internal convenience — it is the public entry point, and the install line in the
# README is what a stranger copies. These four assertions guard the gaps between
# that file, the plugin manifest and the README.


def _marketplace():
    import json

    with (REPO_ROOT / ".claude-plugin" / "marketplace.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def _entry():
    import json

    with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
        name = json.load(fh)["name"]
    return next(p for p in _marketplace()["plugins"] if p["name"] == name)


def test_the_marketplace_is_named_for_the_repository_that_publishes_it():
    """The name is public: users type `<plugin>@<marketplace>` to install.

    It was `abacus-local` while the repo was only ever added from a path on one
    machine. Published from GitHub, a name asserting it is local is simply wrong,
    and every install instruction written against it misleads.
    """
    assert _marketplace()["name"] == "abacus"


def test_every_plugin_source_is_a_relative_path_inside_the_marketplace():
    """A source resolves against the *user's* clone, not the author's disk.

    An absolute path, or one escaping upwards with `..`, works on the machine it
    was written on and nowhere else — a failure that never shows up locally.
    """
    for plugin in _marketplace()["plugins"]:
        source = plugin["source"]
        assert isinstance(source, str) and source.startswith("./"), (
            "%s: source %r must be a relative path starting with ./" % (plugin["name"], source)
        )
        assert ".." not in source, "%s: source escapes the marketplace root" % plugin["name"]


def test_the_marketplace_entry_names_the_public_repository_and_licence():
    """`/plugin` shows these; without them the entry is anonymous in the browser."""
    entry = _entry()
    assert entry.get("repository") == "https://github.com/thijs-hakkenberg/abacus"
    assert entry.get("homepage") == "https://github.com/thijs-hakkenberg/abacus"
    assert entry.get("license") == "MIT"


def test_the_readme_install_block_matches_the_marketplace_it_describes():
    """The copy-pasteable lines are derived here so a rename cannot orphan them.

    Rename the marketplace and forget the README, and the two commands a stranger
    runs first both fail — with nothing in the suite to notice.
    """
    market = _marketplace()
    entry = _entry()
    shorthand = entry["repository"].replace("https://github.com/", "")

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for line in (
        "/plugin marketplace add %s" % shorthand,
        "/plugin install %s@%s" % (entry["name"], market["name"]),
    ):
        assert line in readme, "README.md does not document `%s`" % line
