Feature: A cost figure never travels without its basis
  As a Claude Code user
  I want every recorded dollar figure labelled as a local list-rate estimate, and an unreadable cost omitted rather than zeroed
  So that no figure this plugin writes can be quoted as billing, and no unmeasured task can silently deflate an aggregate

  Background:
    Given the working directory is a beads workspace
    And the task "ab-1" is being tracked with a cost baseline of 10.0

  Scenario: A written cost estimate is always accompanied by its basis
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a cost estimate of 2.5
    And the attribution records the cost basis as a local list-rate estimate

  Scenario: The cost key is named as an estimate
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the recorded cost key is named as an estimate

  Scenario: An unreadable cost writes no dollar figure and no token counts
    Given ccusage cannot be read
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records the cost basis as unavailable
    And the attribution records no cost estimate
    And the attribution records no token counts

  Scenario: An unreadable cost still records how long the task took
    Given ccusage cannot be read
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a duration

  Scenario: A ccusage reading that times out is treated as unreadable
    Given ccusage does not respond in time
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records the cost basis as unavailable

  Scenario: A genuine zero measured between two good readings is recorded
    Given ccusage reports a cumulative session cost of 10.0 over 1000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a cost estimate of 0.0
    And the attribution records the cost basis as a local list-rate estimate

  Scenario: A negative delta is clamped rather than written as a negative cost
    Given ccusage reports a cumulative session cost of 4.0 over 400 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a cost estimate of 0.0

  Scenario: Tool-call counts are written only when the event log holds real events
    Given the event log holds no events for this session
    And ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records no tool-call count

  Scenario: The recorded models are named alongside the cost
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution names the models used

  Scenario: The watcher exits with code 0 when the cost source is missing entirely
    Given the npx executable is not on PATH
    When the Bash watcher observes "bd close ab-1"
    Then the hook exits with code 0
