Feature: Untracked and unattributed work is found and repaired
  As a Claude Code user
  I want to be told which work was never claimed and which closed tasks carry no cost
  So that the gaps the gate cannot see do not accumulate silently in my task history

  Background:
    Given the working directory is a beads workspace
    And no beads issue is in progress

  Scenario: Nothing claimed is reported as the gap that blocks work
    When the audit runs
    Then the audit reports that nothing is claimed
    And the audit reports 0 repairable gaps

  Scenario: A task closed with no attribution at all is reported
    Given the workspace holds a closed issue "ab-0" with no attribution
    When the audit runs
    Then the audit reports "ab-0" as unattributed

  Scenario: A task closed while its attribution was unfinished is reported
    Given the workspace holds a closed issue "ab-0" left unfinished with a cost of 2.5
    When the audit runs
    Then the audit reports "ab-0" as unfinished

  Scenario: Attribution written before the rename is not reported as missing
    Given the workspace holds a closed issue "ab-0" attributed before the rename
    When the audit runs
    Then the audit reports no gap for "ab-0"

  Scenario: An attribution schema this version does not know is left alone
    Given the workspace holds a closed issue "ab-0" with an unrecognised attribution schema
    When the audit runs
    Then the audit reports no gap for "ab-0"

  Scenario: A claim held past the threshold is reported as stale
    Given the workspace holds a claim "ab-1" made 48 hours ago
    When the audit runs
    Then the audit reports "ab-1" as a stale claim

  Scenario: A fresh claim is not reported as stale
    Given the workspace holds a claim "ab-1" made 2 hours ago
    When the audit runs
    Then the audit reports no gap for "ab-1"

  Scenario: Repairing a task with no attribution writes no dollar figure
    Given the workspace holds a closed issue "ab-0" with no attribution
    When the audit runs with repairs enabled
    Then attribution is written to "ab-0"
    And the attribution records the cost basis as unavailable
    And the attribution records no cost estimate
    And the attribution is marked as backfilled
    And the attribution is marked finished

  Scenario: Repairing an unfinished task keeps the figure already banked
    Given the workspace holds a closed issue "ab-0" left unfinished with a cost of 2.5
    When the audit runs with repairs enabled
    Then the attribution records a cost estimate of 2.5
    And the attribution is marked finished

  Scenario: A repair reports a write that beads rejected
    Given the workspace holds a closed issue "ab-0" with no attribution
    And the bd update command exits non-zero
    When the audit runs with repairs enabled
    Then the audit reports that it could not repair "ab-0"

  Scenario: A stale claim is reported but never written to
    Given the workspace holds a claim "ab-1" made 48 hours ago
    When the audit runs with repairs enabled
    Then no attribution is written

  Scenario: A repair never touches an attribution schema this version does not know
    Given the workspace holds a closed issue "ab-0" with an unrecognised attribution schema
    When the audit runs with repairs enabled
    Then no attribution is written

  Scenario: A repair never touches attribution written before the rename
    Given the workspace holds a closed issue "ab-0" attributed before the rename
    When the audit runs with repairs enabled
    Then no attribution is written

  Scenario: A workspace that cannot be read is not reported as clean
    Given the bd list command exits non-zero
    When the audit runs
    Then the audit reports that it could not check
    And no attribution is written

  Scenario: A directory with no beads workspace is not reported as clean
    Given the working directory is not a beads workspace
    When the audit runs
    Then the audit reports that it could not check

  Scenario: The audit exits with code 0 when beads is missing entirely
    Given the bd executable is not on PATH
    When the audit runs with repairs enabled
    Then the audit reports that it could not check
    And the audit exits with code 0
