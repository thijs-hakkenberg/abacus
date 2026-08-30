Feature: Session start primes the agent and baselines the session
  As a Claude Code user
  I want a short orientation at session start and a cost baseline established once
  So that the agent knows how to satisfy the gate without a workflow manual being injected into every session

  Background:
    Given the working directory is a beads workspace
    And ccusage reports a cumulative session cost of 10.0 over 1000 tokens

  Scenario: Session start injects the compact primer
    When the SessionStart hook runs
    Then the injected context names the two commands that satisfy the gate
    And the injected context is under 600 characters

  Scenario: Session start with a task already in progress names that task instead
    Given beads issue "ab-1" is in progress
    When the SessionStart hook runs
    Then the injected context names the task in progress
    And the injected context names the command that closes it

  Scenario: Full mode passes the beads workflow manual through verbatim
    Given the primer mode is configured as full
    And bd prime returns a workflow manual
    When the SessionStart hook runs
    Then the injected context contains the beads workflow manual

  Scenario: Full mode falls back to the compact primer when bd prime fails
    Given the primer mode is configured as full
    And the bd prime command exits non-zero
    When the SessionStart hook runs
    Then the injected context names the two commands that satisfy the gate

  Scenario: Off mode injects nothing
    Given the primer mode is configured as off
    When the SessionStart hook runs
    Then no context is injected

  Scenario: A directory with no beads workspace is not primed
    Given the working directory is not a beads workspace
    When the SessionStart hook runs
    Then no context is injected

  Scenario: Session start adopts a task already in progress and baselines it
    Given beads issue "ab-1" is in progress
    When the SessionStart hook runs
    Then session state records "ab-1" as the current task
    And the recorded cost baseline is attributed to session start

  Scenario: Resuming a session does not overwrite an existing baseline
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And ccusage reports a cumulative session cost of 25.0 over 4000 tokens
    When the SessionStart hook runs for a resumed session
    Then session state records a cost baseline of 10.0

  Scenario: Compaction re-primes without touching the baseline
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And ccusage reports a cumulative session cost of 25.0 over 4000 tokens
    When the PreCompact hook runs
    Then session state records a cost baseline of 10.0
    And the injected context declares the SessionStart event

  Scenario: The prompt statusline names the tracked task on one line
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    When the UserPromptSubmit hook runs
    Then the injected context is a single line
    And the injected context names the task in progress
    And ccusage is never invoked

  Scenario: The prompt statusline is silent when no task is tracked
    When the UserPromptSubmit hook runs
    Then no context is injected

  Scenario: Session start exits with code 0 when bd is not on PATH
    Given the bd executable is not on PATH
    When the SessionStart hook runs
    Then the hook exits with code 0
