# Analysis: commit edges are observed, not inferred

Companion to [../015-commit-edges-are-observed-not-inferred.md](../015-commit-edges-are-observed-not-inferred.md).
The ADR records what was decided and why. This records the **weighing** — the criteria
stated before the options were compared, the option that was eliminated and by which
argument, how confident the evidence justifies being, and what would show the decision
wrong. `## Alternatives Considered` compresses all of that to a paragraph each; this is
the uncompressed form.

Two decisions are weighed here, because they were taken together and the second is only
cheap *because* of how the first went: D1, where the edges live, and D2, how cost per
commit is derived.

---

## Frame

**The question.** abacus records what a task cost and which session claimed it. Commits
appear in the model only as an aggregate absence — 133 across 11 repositories, reported by
`/abacus:audit` and unfixable by construction (adr/013). Give the model commits: capture
each one in the context it happened in, record the m:n task↔commit edges with the evidence
for each, and derive cost per commit without writing an apportioned figure into the store
of record.

**Two decisions, two very different reversibility profiles**, which is what sets the
ceremony for each (§8 of the decision-frameworks note: match rigour to reversibility, not
to how interesting the question feels).

| | D1 — where edges live | D2 — cost per commit |
|---|---|---|
| Kruchten class | **structure** — a storage shape other code will read | **behaviour** — a report line |
| Reversibility | **one-way-ish.** Data accumulates in the user's beads DB from the first commit onwards; a shape change means a migration | **two-way door.** Nothing is written; apportionment happens at read time |
| Cynefin | complicated — analysable, no native precedent in bd | complicated |
| Ceremony warranted | **full scaffold**: criteria before options, matrix, ACH disconfirmation, premortem | **light**: one even-swap and a check against adr/005 |

The asymmetry is the single most useful thing the framing step produced. D2 initially felt
like the harder question — it involves money and apportionment — and it is in fact the
easy one, because no stored data depends on the answer. D1 felt like an implementation
detail and is the one that will be expensive to get wrong.

**Constraints inherited, not chosen.** beads is the store of record (adr/001); a figure
never travels without its basis (adr/005); stdlib only on Python 3.9 (adr/006); the
four-pillar layout (adr/007); `bd create` is not reversible bookkeeping (adr/013); nothing
writes before the settings are acknowledged (adr/014). None of these was up for
renegotiation, and one option below dies on exactly this point.

---

## Criteria

**Stated before the options were listed**, deliberately — Value-Focused Thinking's core
move, and the step that produced this analysis's most useful finding (see Assessment,
D2). Every criterion is traced to a value this repository has already recorded, rather
than invented to fit the answer:

| | Criterion | Source | Weight |
|---|---|---|---|
| **C1** | beads stays the store of record | adr/001 | **veto** |
| **C2** | a wrong write is reversible at the granularity it was written | adr/013's central concern | **high** |
| **C3** | hot-path cost — this runs on `PostToolUse` and on every `Stop` | adr/008's whole subject | high |
| **C4** | bidirectional queryability (task→commits *and* commit→tasks) | the feature's purpose | medium |
| **C5** | relies only on bd mechanics **verified in this repo** | adr/004 (bd is the read interface) | high |
| **C6** | no noise in the issue list the user works in | adr/013's aggregate-report choice | medium |
| **C7** | expressible in a versioned contract | adr/007 | medium |

C1 is a veto rather than a weight: an option that fails it is not a worse option, it is a
different decision — an amendment to adr/001 — and would have to be argued as one.

---

## Options

Including the null and the ones that lost. Listing the null option is not a formality
here; it is genuinely defensible, since the audit already reports the gap honestly.

**D1 — where the edges live**

- **O0 · Null.** Change nothing. The audit keeps reporting untracked commits as an
  aggregate; nobody can ask what a commit cost.
- **O1 · One metadata key per commit** on the task issue:
  `abacus_commit_<sha12>` = `<basis>:<session>:<epoch>`.
- **O2 · Packed list** in one `abacus_commits` value.
- **O3 · One event bead per commit**, linked to the task issue.
- **O4 · `bd kv`** — the beads key-value store.

**D2 — cost per commit**

- **P0 · Null.** Report no per-commit cost.
- **P1 · Equal share within the task**, computed at read time.
- **P2 · Lines-weighted** by `git show --numstat`.
- **P3 · Equal share, publishing the denominator** — surfaced *by* the criteria step; see
  Assessment.

---

## Assessment

### D1

| Option | C1 record | C2 reversible | C3 hot path | C4 bidirectional | C5 verified | C6 quiet | C7 contract |
|---|---|---|---|---|---|---|---|
| **O1** one key per commit | ✓ | **✓✓** | ✓ | ~ | **✓✓** | ✓ | ✓ |
| O2 packed list | ✓ | ✗✗ | ✓ | ✗ | ✗ | ✓ | ✓ |
| O3 event beads | ✓ | ✗✗ | ✗ | **✓✓** | ~ | ✗✗ | ✓ |
| O4 `bd kv` | **~ (veto)** | ~ | ✓ | ~ | ✗ | ✓✓ | ~ |
| O0 null | ✓ | ✓✓ | ✓✓ | ✗✗ | ✓✓ | ✓✓ | ✓✓ |

O0 is dominant on every criterion except the one the feature exists for, which is the
correct shape for a null option and the reason it is listed: it makes explicit that the
whole case rests on C4 being worth anything at all. It is worth it — "which commits did
this task produce" is unanswerable today and is the question a cost tracker is expected to
answer.

**The disconfirmation pass (ACH) is what actually moved the answer.** O1 was the anchored
candidate — the shape reached for first — so the honest exercise is to attack it and to
steelman the others.

- *Against O1:* it is the weakest option on C4. **Survives.** commit→tasks needs one
  `bd list --all --json` scan, which the audit already performs once per run; the cost is
  a scan the codebase makes anyway.
- *For O2:* one key is tidier than 133. **Eliminated.** Merging a sha into one value needs
  a read-modify-write. adr/011's `carried_partial` is the same pattern in this codebase and
  is the most delicate code in it; two terminals committing concurrently would lose edges
  outright. The unit of write (the whole list) exceeds the unit of the fact (one edge),
  which is precisely the C2 failure.
- *For O3:* it is the only option that wins C4 outright, and event beads are a real
  pattern. **Eliminated as a trade-off, not a defect** — adr/013's finding is that *issue
  creation is not reversible bookkeeping*, a property of `bd create` and unchanged by our
  evidence now being observed rather than inferred. 133 commits means 133 beads in the list
  the user works in (C6). Recorded in the ADR as the upgrade path, which is the right
  disposition for an option that loses on weights rather than on facts.
- *For O4:* it keeps the issue list perfectly clean and it is inside the beads DB.
  **Eliminated, and it should not have been shortlisted** — the finding this pass exists
  to produce. kv is inside the DB but is *not an issue*, so choosing it means amending
  adr/001 (C1 veto). And it does not even win the criterion it was shortlisted for: it
  **inverts** the C4 weakness (commit→task direct, task→commit needs a scan) rather than
  removing it. An adr/001 amendment plus unverified mechanics (C5) for **zero net gain**.

**Which instrument caught what:** the ACH disconfirmation step eliminated O4, an option I
had shortlisted and would otherwise have written up as a plausible runner-up. Attacking
one's own shortlist is the cheapest step in the whole scaffold and the only one that
changed the option set.

### D2

The **even-swap** settled it in one move: neutralise implementation cost by assuming
`--numstat` were free — does P2 win? **No.** A lockfile, a generated file or a vendored
directory swamps the weighting, so the figure becomes *less* accurate while *looking* more
precise. That is adr/005's named failure mode, "a wrong answer wearing the costume of a
measurement". P2 loses on its own merits and not on cost, which matters for the write-up:
had it lost only on cost, it would belong in Deferred as a later enhancement. It does not.
It belongs in Rejected.

**What stating criteria before options surfaced, and neither original option contained.**
Working from "what makes a per-commit figure trustworthy" rather than from "equal or
weighted" produced P3: the honesty lever is not the weighting, it is **publishing the
denominator**.

```
b0cff66  $0.20  (1 of 4 commits in abacus-7 · task total $0.8123 · apportioned-equally-within-task)
```

Self-auditing — the user can see the apportionment and disagree with it — nearly free, and
better than either weighting scheme, because it makes the method visible instead of making
the number look better. Borrowed from specification-curve analysis: where a commit carries
edges to several tasks, show the **set** of shares and state plainly that per-commit costs
do not sum to the repository total under m:n. Omitted entirely — never `$0.00` — when the
task's `abacus_cost_basis` is `unavailable`.

P3 is the second instrument finding: **the option that won D2 did not exist when D2 was
framed as a choice between two.**

---

## Sensitivity and Trade-off Points

ATAM's distinction, and worth keeping separate because they call for different responses:
a sensitivity point is where one decision drives one outcome (watch it), a trade-off point
is where one decision drives two outcomes in opposite directions (document it).

**Sensitivity points**

- **C1 for O4.** kv-vs-issue is the single parameter that decides whether O4 is an option
  at all. Nothing else about O4 matters until adr/001 is amended.
- **Rail 1 (seed, never attribute).** The whole `observed` tier's honesty hangs on one
  branch. Without it, the first git command of a session attributes the repository's entire
  history to whatever task is claimed. Highest-consequence single line in the feature;
  it lives in `commit_capture.capture` and is asserted directly.
- **`claimed_at` readability (rail 2).** If it cannot be parsed, no `observed` edge can be
  justified. Correctly resolves to writing nothing rather than to writing anyway.

**Trade-off points**

- **O3, precisely.** Bidirectional queryability ↔ reversibility and issue-list quiet. Both
  directions are real; neither is a bug. This is what makes it an upgrade path rather than
  a rejected idea, and why the ADR says so in those terms.
- **Prefilter width in the watcher.** Widening `bd` to `bd`-or-`git` buys capture coverage
  and costs roughly double the commands reaching `parse_events()`. Bounded: pure string
  work, ahead of any subprocess.
- **`max_per_boundary` (50).** Higher captures more legitimate large pushes; lower is
  safer against a rebase or a pull being attributed. An unreadable value falls back to the
  default rather than to unlimited, because a typo must not silently widen what gets
  attributed — and `0` is taken literally, since anyone wanting capture off has
  `commits.enabled`.
- **Sweeping at `Stop`.** Catches boundaries no verb list matched, and costs a filesystem
  walk at the end of every turn in every project on the machine. Resolved by ordering
  capture's cheap checks so the free one (`beads.workspace_root`, a filesystem walk) runs
  before the expensive one (`gitlog.repo_root`, a subprocess) — a directory abacus has no
  business in costs zero subprocesses.

---

## Evidence Certainty

Calibrated deliberately, because "verified" and "assumed" were doing very different work
in the argument and the prose does not distinguish them on its own.

| Claim | Basis | Certainty |
|---|---|---|
| No Claude Code hook fires on a git commit | 27-member event enum extracted from the v2.1.258 binary; the 5 `PostCommit` hits are React Fiber profiler internals | **high** |
| `PostToolUse` fires only on success; failure routes to an unwired event | documented behaviour + this plugin's own `hooks.json` | high |
| `rev-list --reverse OLD..HEAD` includes merges | run here: 6 commits, 2 merges | **high** |
| `%(trailers:key=…,valueonly,separator=…)` works | run against `Co-Authored-By` | high |
| `--set-metadata` merges rather than replaces | verified previously, adr/007 cites it | high |
| `--unset-metadata` removes one key | verified | high |
| metadata is writable on a **closed** issue | verified | high |
| bd has no issue↔commit relation | `bd link`/`bd dep add` are issue↔issue; `--external-ref` is a scalar | **medium-high** — absence of a feature is harder to prove than its presence |
| `core.hooksPath` unset at all three scopes; one repo of 170 has real hooks | inspected | high |
| bd imposes no per-issue metadata key-count or size ceiling | **not verified** — this is the premortem | **low** |
| A second terminal's commits land on the wrong claim | reasoned from the mechanism, not observed | medium |

The one **low** entry is load-bearing for D1 and is why Phase 5 exists.

---

## Assumptions and Falsifiers

Each paired with the observation that would kill it, so a later reader can check rather
than re-argue.

1. **bd imposes no practical ceiling on metadata keys per issue.**
   *Falsifier:* writing ~200 `abacus_commit_*` keys to one scratch issue on a real
   `bd init` database errors, truncates, or degrades read latency noticeably.
   *Response if falsified:* O2's packing problem returns, and the answer is probably
   per-key with periodic rollup, not O2 — but the decision reopens.
   → **This is the premortem, and it is Phase 5's test.**
2. **The verb list plus periodic sweeps catch essentially every commit.**
   *Falsifier:* a commit in a governed repository with no edge after a `Stop` and a
   `SessionEnd`. *Note:* by design this fails **safe** — a missed commit is an unrecorded
   edge, never a wrong one.
3. **`Beads-Task:` trailers are rare today, so `declared` is mostly latent.**
   *Falsifier:* they turn out common, in which case the m:n tier carries more weight than
   assumed and deserves its own documentation in the read surface.
4. **Rail 2 alone neutralises `git pull`.**
   *Falsifier:* an upstream commit with a committer date *after* our claim — a rebase, a
   clock skew, an amended date — slipping through. Rail 3's cap is the second line of
   defence precisely because rail 2 trusts a timestamp.
5. **Equal-share apportionment is good enough given a published denominator.**
   *Falsifier:* a user reads a per-commit figure as authoritative despite the label. Cheap
   to revisit — nothing is stored (D2's two-way door).
6. **`commits.*` does not need to be a governed key.**
   *Falsifier:* a user is surprised to find commit shas in their beads metadata after
   acknowledging only the six existing settings. Would be answered by adding the key to
   `consent.GOVERNING_KEYS`, which is a one-line change and a re-ask.

---

## Decision and Warrant

**D1: one metadata key per commit on the task issue, `abacus_commit_<sha12>` =
`<basis>:<session-id>:<epoch>`, constructed only in `attribution.build_commit_edges`.**

*Warrant (Toulmin — the step that connects the evidence to the claim, and the one ADRs
routinely leave implicit):* this repository's governing constraint on writes to the store
of record is **reversibility at the granularity of the fact**, established by adr/013 and
adr/011. O1 is the only option whose **unit of write equals the unit of the fact** — one
edge, one key, undone by one `--unset-metadata`. Every rejected option's unit of write is
larger than one edge: O2 writes a list, O3 writes an issue, O4 writes outside the issue
model entirely.

*Accepted downside:* commit→tasks requires a full `bd list --all --json` scan. Accepted
because the audit already performs exactly that scan once per run, so the cost is
incremental rather than new.

*Confidence: high* — strong verified evidence across the mechanics that matter, high
agreement across criteria, and the one low-certainty claim isolated into a falsifiable
premortem test rather than assumed away.

**D2: equal share within the task, published with its denominator, derived at read time,
and no lines-weighted knob.**

*Warrant:* adr/005 already establishes that this plugin's obligation is to label a figure
honestly rather than to make it look precise. P3 discharges that obligation better than
either weighting scheme, and it costs less than P2.

*Accepted downside:* a commit that changed one line and a commit that changed a thousand
carry the same share. Visible in the output rather than hidden by it, which is the point.

**Capture mechanism and scope** were selected by the user: HEAD watermark, no writes into
any user repository, forward capture only. The analytical work above sits inside those
choices; it did not make them.

---

## Consequences

**Follow-on decisions this one creates.**

- The three rails are now the load-bearing correctness argument for `observed`. Rails 1
  and 3 belong in `commit_capture.capture` (properties of the HEAD *move*); rail 2 belongs
  in `attribution.build_commit_edges` (a property of a single *commit*, checked per commit
  and per tier). Putting rail 2 in capture would apply it to a batch and let one recent
  commit vouch for an older one.
- One shared `capture()` with four callers, not a watcher copy and a sweep copy. Two
  copies would drift, and the one that drifted would be the one nobody was watching.
- The `Stop` sweep must sit **above** `stop_reconcile`'s `if not current: return 0`. A
  `declared` trailer names its own tasks and needs no claim, so gating the sweep on
  `current_task` would make the *strongest* tier of evidence the only one a sweep could
  miss. This is the least obvious ordering constraint in the feature and has a test whose
  docstring says so.
- `SessionEnd` must sweep before the partial write (which clears the claim `observed`
  needs) and before any `bd dolt push` (or the sync ships a session without the edges it
  just recorded).
- `SessionStart` is also wired to `PreCompact`, so it must **not** blind-reseed: on a
  fresh session there is no watermark and rail 1 makes the call a seed; on a resume or a
  compaction there is one and the same call sweeps. A long session that compacts is
  precisely the one most likely to have commits to lose.
- The audit's narrowing must be per-sha and **workspace-wide**, unioned across all issues:
  m:n means there is no *the* issue for a commit. Reading through
  `attribution.commit_edges` also makes a malformed value vouch for nothing.

**What was deferred, and why it is deferral rather than rejection.**

- Retrospective reconciliation of the 133 (user-selected scope).
- An opt-in git hook for commits made outside Claude — ceiling is "a commit happened".
- Event beads (O3) — a live trade-off, revisit if commit→task becomes hot.
- Passing the session id to `bd close --session`, which bd accepts natively and
  `beads.close()` does not yet use.

Lines-weighted apportionment (P2) is in **none** of those lists: it was rejected on merit.

---

## Y-statements

**D1.** In the context of recording m:n task↔commit edges where beads offers no native
commit relation, facing a choice between issue metadata, event beads and the kv store, we
chose **one metadata key per commit on the task issue**, to keep every edge individually
reversible and to rely only on bd mechanics already verified in this repository, accepting
that commit→task lookup requires a full-issue scan rather than an index.

**D2.** In the context of reporting what a single commit cost, where cost is measured per
task and the task↔commit relation is m:n, facing a choice between equal shares and
lines-weighted apportionment, we chose **equal shares published with their denominator and
computed at read time**, to make the method visible rather than the number precise,
accepting that a one-line commit and a thousand-line commit carry the same share.

**Capture.** In the context of needing the session a commit was made in, where no Claude
Code hook fires on a commit and a git hook has no Claude environment, facing a choice
between parsing stdout, installing a git hook and asking git directly, we chose a **HEAD
watermark compared at hook boundaries**, to make capture deterministic and to write
nothing into the user's repository, accepting that a commit made outside every boundary
we observe is delayed until the next sweep or lost rather than invented.
