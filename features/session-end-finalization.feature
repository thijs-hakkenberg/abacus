Feature: Unfinished work is recorded before the session ends
  As a Claude Code user
  I want a task still open when my session closes recorded as unfinished rather than lost
  So that a task I work on across several sittings still reports its whole cost when I finally close it

  Background:
    Given the working directory is a beads workspace
    And the task "ab-1" is being tracked with a cost baseline of 10.0

  Scenario: A task still open at session end is recorded as unfinished
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the SessionEnd hook runs
    Then attribution is written to "ab-1"
    And the attribution for "ab-1" is marked unfinished
    And the attribution records a cost estimate of 2.5

  Scenario: A task recorded as unfinished accumulates when it is finally closed
    Given "ab-1" already carries unfinished attribution of 2.5 over 4000 tokens
    And ccusage reports a cumulative session cost of 11.25 over 2000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a cost estimate of 3.75
    And the attribution is marked finished

  Scenario: A task already finalised is not charged a second time
    Given "ab-1" already carries finished attribution of 2.5 over 4000 tokens
    And ccusage reports a cumulative session cost of 11.25 over 2000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a cost estimate of 1.25

  Scenario: A close the watcher never saw is repaired at Stop
    Given beads issue "ab-1" has been closed outside this session
    And ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Stop hook runs
    Then attribution is written to "ab-1"
    And the attribution is marked finished
    And no task is recorded in session state

  Scenario: A task moved out of progress without being closed is recorded as unfinished
    Given beads issue "ab-1" has been moved back to open outside this session
    And ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Stop hook runs
    Then the attribution for "ab-1" is marked unfinished

  Scenario: Stop leaves a task that is still in progress alone
    Given beads issue "ab-1" is in progress
    When the Stop hook runs
    Then no attribution is written

  Scenario: Stop does nothing when beads cannot be reached
    Given the bd list command exits non-zero
    When the Stop hook runs
    Then no attribution is written
    And session state records "ab-1" as the current task

  Scenario: Stop spawns no subprocess when nothing is being tracked
    Given no task is being tracked in session state
    When the Stop hook runs
    Then beads is never invoked

  Scenario: Stop does not act on its own re-invocation
    Given the payload marks the stop hook as already active
    When the Stop hook runs
    Then no attribution is written

  Scenario: Session end syncs even when every task was closed cleanly
    Given no task is being tracked in session state
    And session end is configured to push
    When the SessionEnd hook runs
    Then beads is asked to push

  Scenario: Session end does not push when syncing is off
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the SessionEnd hook runs
    Then beads is not asked to push

  Scenario: Session end exits with code 0 when the push fails
    Given session end is configured to push
    And the bd dolt command exits non-zero
    When the SessionEnd hook runs
    Then the hook exits with code 0
