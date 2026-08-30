Feature: Untracked edits are refused at the tool boundary
  As a Claude Code user
  I want edits blocked whenever no beads task is in progress
  So that every change I make is attributable to tracked work without my having to remember to claim one

  Background:
    Given the working directory is a beads workspace

  Scenario: An edit with no task in progress is denied with remediation
    Given no beads issue is in progress
    When the PreToolUse gate runs for an Edit
    Then the gate denies the tool call
    And the denial reason names the command to claim an existing task
    And the denial reason names the command to create a new task
    And the denial reason names the environment variable that bypasses the gate

  Scenario: An edit with a task in progress is allowed silently
    Given beads issue "ab-1" is in progress
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call
    And the gate prints nothing

  Scenario: A Write is gated on the same terms as an Edit
    Given no beads issue is in progress
    When the PreToolUse gate runs for a Write
    Then the gate denies the tool call

  Scenario: A Read is not gated
    Given no beads issue is in progress
    When the PreToolUse gate runs for a Read
    Then the gate allows the tool call
    And the gate prints nothing

  Scenario: A missing beads executable allows the edit
    Given the bd executable is not on PATH
    And no beads issue is in progress
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call

  Scenario: A beads query that exits non-zero allows the edit
    Given the bd list command exits non-zero
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call

  Scenario: A directory with no beads workspace is not gated by default
    Given the working directory is not a beads workspace
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call

  Scenario: A directory with no beads workspace is denied when configured to block
    Given the working directory is not a beads workspace
    And the gate is configured to block projects without a beads workspace
    When the PreToolUse gate runs for an Edit
    Then the gate denies the tool call
    And the denial reason names the command that initialises a beads workspace

  Scenario: The kill switch disables the gate entirely
    Given no beads issue is in progress
    And the plugin is disabled by environment variable
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call
    And the gate prints nothing

  Scenario: A claim the watcher never saw is given a cost baseline by the gate
    Given beads issue "ab-1" is in progress
    And no task is being tracked in session state
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call
    And session state records "ab-1" as the current task
    And the recorded cost baseline is attributed to the gate

  Scenario: The gate exits with code 0 on a malformed payload
    Given the payload is not valid JSON
    When the PreToolUse gate runs for an Edit
    Then the hook exits with code 0
