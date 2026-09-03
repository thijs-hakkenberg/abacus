# ADR 007: Describe the repo through four pillars and a manifest, and test that they agree

## Status

Accepted

## Date

2026-08-06

## Context

This plugin is small — around 1,400 lines of hooks — but almost none of what makes
it correct is visible in those lines. The gate denies edits in exactly one of six
cases and allows in the other five; a cost figure is omitted rather than zeroed;
`--set-metadata` is known to merge rather than replace, which is why a
read-modify-write is safe. Each of those is a decision with a reason behind it, and
a reader who has only the code has to reverse-engineer the reason or, more likely,
guess it wrong and "fix" it.

So the repo needs prose. The question is what shape it takes, because undated,
unowned design notes at the top level of a repository are where reasoning goes to
rot: `NOTES.md`, `DESIGN.md`, `thoughts.md`, each one true when written and
unfalsifiable afterwards.

Four kinds of question keep recurring, and each wants a different artefact:

- *Why is it like this?* → a dated, immutable decision record
- *What does it do?* → a behavioural description that can be executed
- *Where does it stop?* → an explicit statement of boundaries and neighbours
- *How do I talk to it?* → a versioned interface description with an SLA

## Decision

Adopt a fixed four-pillar layout plus a machine-readable manifest, and make the
relationships between those artefacts assertable by a test.

```
spec.manifest.yaml          repo ROOT — the repo as one node in an EA graph
adr/NNN-lowercase-title.md  why
features/*.feature          what, executably
contexts/                   abacus-canvas.md + context-map.d2 — boundaries
contracts/{input,output}/   one .md per interface, versioned, with an SLA
```

Only `README.md`, `CLAUDE.md` and `CHANGELOG.md` are permitted as loose markdown at
the top level. A design note goes in an ADR or a contract, or it does not get
written.

- **ADRs use Nygard structure**: `## Status`, `## Date`, `## Context`,
  `## Decision`, `## Consequences` with `### Positive`/`### Negative`/`### Neutral`
  subsections, and `## Alternatives Considered`. Filenames are
  `NNN-lowercase-title.md` with no `ADR-` prefix, so they sort numerically and read
  as sentences. A superseding decision is recorded as a dated `## Addendum:` on the
  original rather than by rewriting it — the reasoning behind a *reversed* decision
  is most of an ADR's value, and a rewrite destroys exactly that.
- **The canvas follows the ddd-crew Bounded Context Canvas**, a public template,
  with its eleven H2 sections present and in order. A canvas is read top to bottom;
  reordering it changes what a reader takes away.
- **`spec.manifest.yaml` sits at the repo root** and declares the repo as an
  architecture node: `name`, `description`, `type`, `ea_layer`, `owner`, `status`,
  `business_capability`, `artefacts:` (British spelling throughout),
  `interfaces.inbound[]`/`outbound[]` with `sla.latency_p99_ms`, and
  `dependencies[]` pointing at the ADR that documents each one. Root rather than
  under `contracts/` because it describes the whole repo, not one interface.
- **`tests/unit/test_spec_conformance.py` enforces the layout**, since nothing
  external does. Its assertions are deliberately about *agreement between*
  artefacts, which is the class of error a human reviewer will not catch by
  reading: the four pillars exist and are non-empty; `spec.manifest.yaml` parses
  and every path under `artefacts:` resolves; every hook script the manifest names
  exists on disk; every `sla.latency_p99_ms` equals its `hooks.json` timeout ×
  1000; the canvas has its eleven sections in order; every `.feature` parses and is
  collected; ADR filenames match the convention.
- **The feature space is executable.** Every scenario in `features/` binds to a
  pytest-bdd step definition that drives the real hook as a subprocess, and an
  unbound scenario **fails rather than skips** — the conformance test asserts that
  too. A `features/` directory hand-synced with a separate test suite is
  documentation pretending to be a test.

## Consequences

### Positive
- The manifest/`hooks.json` SLA check catches a real and otherwise-invisible bug
  class: changing a hook timeout without changing the contract that promises a
  latency, leaving two files that disagree and no reader who would notice.
- The four pillars give every kind of prose an obvious home, which is what keeps
  top-level markdown from accumulating. The test enforces that mechanically rather
  than relying on discipline.
- ADR addenda rather than rewrites mean a reversal keeps its own history. Reading
  `adr/003`'s addenda tells you what was believed, what broke it, and what replaced
  it — three facts a clean rewrite would have flattened into one.
- An executable feature space cannot silently describe behaviour the software has
  lost, because the scenario would fail. That is a stronger guarantee than any
  review process gives.

### Negative
- **Nothing checks freshness.** The conformance test proves the artefacts agree
  with *each other*, not that they still describe the software. All twelve ADRs
  could be simultaneously obsolete and the suite would stay green. This is the
  failure most likely to happen and the one only a human catches; it is stated here
  rather than glossed because a green test invites the opposite assumption.
- Twelve ADRs, seven feature files, a canvas, a context map and ten contracts is a
  real documentation load for a single-purpose plugin. Every behavioural change now
  has a prose cost, and a change made in a hurry will skip it.
- The layout is self-imposed, so it is also self-enforced. If the conformance test
  is ever deleted or marked `xfail`, nothing remains.

### Neutral
- `spec.manifest.yaml` is YAML while the runtime config is JSON. Deliberate: the
  manifest is read only by tests, where `pyyaml` is available, and the config is
  read by stdlib-only hooks on Python 3.9 with no `tomllib` (adr/006).
- This repo also doubles as its own local marketplace via
  `.claude-plugin/marketplace.json`, which is a Claude Code packaging concern and
  orthogonal to this layout.

## Alternatives Considered

### Alternative 1: A single `DESIGN.md`

Rejected. One file cannot be immutable and current at the same time, which is the
whole tension the ADR form resolves by being append-only and dated. A single
document also has no natural place to record a decision that was *reversed*: the
old text either survives and contradicts the new, or is deleted along with the
reasoning that made the reversal interesting.

### Alternative 2: Prose only, no conformance test

Rejected. Every mechanical drift this test catches — a manifest pointing at a
renamed directory, a timeout changed on one side, a canvas section quietly dropped,
a `.feature` with a typo that stops it parsing — is invisible in review, because
catching it requires holding two files in your head at once. Those are precisely
the errors that accumulate between audits.

### Alternative 3: Generate the artefacts from the code

Rejected. Documentation derived from code can only restate what the code says, and
the valuable content here is exactly what the code cannot say: the alternative that
was rejected, the measured figure that settled an argument, the failure mode a
constraint exists to prevent. A generator would have produced none of the content
in these ADRs that has since prevented a mistake.

## Addendum: 2026-09-03 — the "why" pillar gains an `analysis/` subdivision

An extension, not a reversal, but recorded as an addendum because the mechanism is the
same one this ADR prescribes for a reversal.

**What changed.** `adr/analysis/NNN-<same-slug>.md` may now hold the full weighing behind
an ADR: the criteria stated before the options, the assessment matrix, the option that was
eliminated and by which argument, calibrated evidence certainty, the premortem and the
falsifiers, and a closing Y-statement. First instance: adr/015 and its companion.

**Why it is a subdivision rather than a fifth pillar.** This ADR says a design note goes
in an ADR or a contract, or it does not get written — so a top-level `decisions/`
directory would contradict it directly. The reasoning behind a decision is not a fifth
kind of question; it is the same question ("why is it like this?") at a resolution
`## Alternatives Considered` cannot hold. `## Alternatives Considered` compresses each
rejected option to a paragraph, which is right for a reader deciding whether to reopen a
decision and wrong for one who has decided to. **The four-pillar count is unchanged.**

**Why it needed new assertions.** `_adrs()` globs `adr/*.md` non-recursively and the
loose-markdown check globs the root only, so companion files would otherwise have been
**entirely unchecked** — which is what this repo calls documentation pretending to be a
test. Three assertions were added, all of the "agreement between artefacts" class the rest
of the suite is built from: the filename convention plus **no orphan** (an `NNN` with no
`adr/NNN-*.md`); the scaffold sections are present; and **the parent ADR links to its
companion**, since an unreachable archive is the same as no archive.

Assertion 1 is **one-directional** on purpose: an ADR without a companion is legal, an
analysis without an ADR is not. Backfilling companions for adr/001–014 is explicitly out of
scope — those analyses were not recorded at the time, and reconstructing them now would be
invention, which is the error adr/013 refuses.

In the spirit of this ADR's own Negative consequence: the new assertions check that an
analysis is present, well-formed and reachable. They do not check that it is sound or
still true. One can be wholly superseded and the suite stays green.
