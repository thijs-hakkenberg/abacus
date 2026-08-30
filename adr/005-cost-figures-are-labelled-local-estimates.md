# ADR 005: A cost figure never travels without its basis, and an unreadable cost is omitted rather than zeroed

## Status

Accepted

## Date

2026-08-05

## Context

Two failure modes of cost reporting are much worse than not reporting cost at
all, and both were observed in prior art rather than imagined.

**A local estimate read as a billing statement.** A session-level cost tracker the
author built earlier had to remove dollar figures from its per-project breakdown
outright, because `total_cost` sitting next to a project label reads as an
authoritative spend claim when it is a this-device, list-rate, ccusage-derived
number. The reviewer's words were: *"it is just straight not true anyways, so why
display that figure."* The resolution there was to delete the number and report
percentage-of-effort instead.

This plugin cannot take that resolution. Per-task cost attribution *is* the
feature; there is no percentage-of-effort substitute for "what did this task
cost". So the number stays and the mislabelling risk has to be handled another
way.

**A zero that means "we could not measure".** If ccusage cannot be read and the
code writes `0.0` anyway, the result is a task that ran for an hour recorded as
free. A `$0.00` against real work is a wrong answer wearing the costume of a
measurement — it produces no question, no investigation, and it silently
corrupts any aggregate built over it. An absent key produces a question.

This second mode was not hypothetical. It occurred twice during development and
was caught both times:

- **`abacus_tool_calls: 0`** was being written whenever the OTEL log was readable but
  held no events for the session. A readable log with no events is not evidence
  that no tools ran — OTEL may be off, sampling, or lagging.
- **A cache hit was being written as a measured `0.0`** for any task claimed and
  closed inside the ccusage cache TTL, with `abacus_cost_basis` claiming it was a
  real reading. This is the bug documented in adr/003's addendum, and it hit the
  most common shape of work rather than an edge case.

## Decision

Two rules, applied to every figure this plugin writes.

**1. The dollar figure never travels alone.** `abacus_cost_usd_estimate` is always
written beside `abacus_cost_basis`. Three things enforce the labelling:

- The key is named `..._estimate`, not `..._cost`.
- `abacus_cost_basis` is `ccusage-local-list-rate` — naming the tool, the locality,
  and the pricing method in one token, so a reader who quotes the number without
  the basis has visibly discarded information.
- Every user-facing surface that renders the figure (`skills/cost-report`,
  `commands/status`) states that it is a local estimate and not billing.

**2. An unreadable cost is omitted, never zeroed.** When ccusage cannot be read,
`attribution.build_metadata` writes `abacus_cost_basis=unavailable` and **no dollar
figure and no token counts at all**. The same rule applies to every optional
figure:

- OTEL enrichment writes `abacus_tool_calls`/`abacus_active_min` only when at least one
  real event was seen (`if stats and (stats["tool_calls"] or stats["api_calls"])`).
- The closing ccusage read bypasses the cache (`fresh=True`) so a cache hit can
  never be presented as a measurement (adr/003 addendum).
- `ccusage.diff` marks its result `ok=False` if *either* input snapshot was
  unreliable, so a half-measured delta is never written as a whole one.

**Duration is exempt, and deliberately so.** `abacus_duration_min` is wall-clock
between claim and finalisation and does not depend on ccusage at all. A task's
elapsed time is knowable even when its cost is not, so it is always written. This
is why a `abacus_cost_basis=unavailable` record is still useful: it says how long the
work took and refuses to guess what it cost.

**A genuine measured zero is honest and is written.** The forbidden case is an
*unreadable* cost written as zero, not a real zero. A brand-new session ccusage
has not indexed yet returns `ok=True` with zeros and diffs correctly; two readings
taken milliseconds apart with no model turn between them genuinely differ by
nothing. During E2E verification a real `cost: 0` was investigated rather than
assumed to be a bug: the stored baseline (50.0748) was compared against the live
ccusage total (50.1874), establishing that the claim and SessionEnd had run inside
one shell command with no turn in between, so the cumulative total really was
identical at both readings.

## Consequences

### Positive
- No aggregate built over this metadata can be silently deflated by unmeasured
  tasks, because unmeasured tasks carry no figure to average in. They are
  countable (`abacus_cost_basis=unavailable`) rather than invisible.
- A reader who sees `abacus_cost_usd_estimate` also sees, in the adjacent key, that
  it is a local list-rate estimate. Mislabelling it as billing requires actively
  dropping a field.
- Duration and token counts survive a ccusage outage, so a task is never entirely
  unaccounted for.
- The rule is testable and is tested: the suite asserts that a broken ccusage
  produces `unavailable` with no cost key, that a short task is not recorded as
  free, and that a session with ccusage broken still tracks time.

### Negative
- Reports must handle absent keys everywhere, which is more code than defaulting
  to zero would be, in every consumer including future ones. Accepted: this is
  the cost of the property, and it is paid once per consumer rather than once per
  wrong number.
- `abacus_cost_usd_estimate` is still a dollar figure in a metadata field, and
  metadata can be read by anything. Labelling reduces but cannot eliminate the
  risk of it being quoted out of context. This is the residual risk this plugin
  accepts where the earlier tool deleted the number entirely — the difference is
  that per-task cost is this plugin's entire purpose.
- Aggregating across users or machines would compound the locality problem
  (different pins, different pricing tables). Not supported and not attempted in
  v1.

### Neutral
- `abacus_schema` accompanies every write, so a future change to these conventions
  is detectable rather than requiring inference from which keys are present.
- Partial figures carry `abacus_partial=true` and are accumulated on the eventual
  close, so an interrupted task is neither lost nor double-counted (adr/011).

## Alternatives Considered

### Alternative 1: Drop dollar figures entirely, as the earlier tool did

Rejected. That tool could substitute percentage-of-effort because its consumer
wanted relative attribution across projects. Here the question *is* "what did this
task cost", and a percentage does not answer it. Keeping the number and making it
structurally impossible to un-label it is the compromise.

### Alternative 2: Write `0.0` with a flag like `abacus_cost_measured=false`

Rejected. It puts a number in the field where a number means money, and every
naive consumer — a `jq` one-liner, a spreadsheet sum, an agent reading the
metadata — gets a wrong total unless it happens to check the sibling flag. An
absent key breaks such consumers loudly, which is the desired behaviour.

### Alternative 3: Refuse to finalise at all when cost is unreadable

Rejected. It would discard the duration and the fact that the task ran, both of
which are known and useful, in order to avoid writing a figure that is simply
omitted anyway. It would also mean a ccusage outage leaves tasks with no
attribution record at all, making the outage invisible after the fact.
