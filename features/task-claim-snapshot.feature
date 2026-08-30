Feature: Claiming a task takes a cost baseline
  As a Claude Code user
  I want a cost reading captured the moment I claim a task
  So that whatever I spend from then on can be attributed to that task and nothing earlier can be

  Background:
    Given the working directory is a beads workspace
    And ccusage reports a cumulative session cost of 10.0 over 1000 tokens

  Scenario: A claim records the task and its baseline
    When the Bash watcher observes "bd update ab-1 --claim"
    Then session state records "ab-1" as the current task
    And session state records a cost baseline of 10.0

  Scenario: A claim chained after another command is still detected
    When the Bash watcher observes "cd sub && bd update ab-1 --claim"
    Then session state records "ab-1" as the current task

  Scenario: A claim prefixed with an environment assignment is still detected
    When the Bash watcher observes "BEADS_DIR=/other bd update ab-1 --claim"
    Then session state records "ab-1" as the current task

  Scenario: A claim spelled as a status change is detected
    When the Bash watcher observes "bd update ab-1 --status in_progress"
    Then session state records "ab-1" as the current task

  Scenario: A quoted mention of a claim command is not a claim
    When the Bash watcher observes "echo \"bd update ab-1 --claim\""
    Then no task is recorded in session state

  Scenario: A read-only beads command is not a task boundary
    When the Bash watcher observes "bd ready --json"
    Then no task is recorded in session state

  Scenario: A command with no beads invocation costs no subprocess
    When the Bash watcher observes "ls -la"
    Then no task is recorded in session state
    And ccusage is never invoked

  Scenario: Re-claiming the task already in progress does not move the baseline
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And ccusage reports a cumulative session cost of 25.0 over 4000 tokens
    When the Bash watcher observes "bd update ab-1 --claim"
    Then session state records a cost baseline of 10.0

  Scenario: Claiming a second task finalises the first as unfinished
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And ccusage reports a cumulative session cost of 25.0 over 4000 tokens
    When the Bash watcher observes "bd update ab-2 --claim"
    Then attribution is written to "ab-1"
    And the attribution for "ab-1" is marked unfinished
    And session state records "ab-2" as the current task

  Scenario: The watcher exits with code 0 when ccusage cannot be read
    Given ccusage cannot be read
    When the Bash watcher observes "bd update ab-1 --claim"
    Then the hook exits with code 0
