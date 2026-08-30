Feature: Closing a task writes its cost, tokens and duration
  As a Claude Code user
  I want the cost of a task recorded onto the beads issue when I close it
  So that per-task spend travels with the issue and syncs to every other machine without a separate store

  Background:
    Given the working directory is a beads workspace
    And the task "ab-1" is being tracked with a cost baseline of 10.0

  Scenario: A close writes the cost delta as issue metadata
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then attribution is written to "ab-1"
    And the attribution records a cost estimate of 2.5
    And the attribution records the cost basis as a local list-rate estimate
    And the attribution is marked finished

  Scenario: A close writes every token dimension
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a total token count
    And the attribution records input, output, cache-read and cache-write token counts

  Scenario: A close records the schema version and the session that did the work
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records the schema version
    And the attribution records the session id

  Scenario: A close on the line after a directory change is detected
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "cd /tmp/elsewhere" and "bd close ab-1" on separate lines
    Then attribution is written to "ab-1"
    And the attribution records a cost estimate of 2.5

  Scenario: A task mentioned in a heredoc body is not closed
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "cat > notes.md <<'EOF'" and "bd close ab-1" on separate lines
    Then no attribution is written
    And session state records "ab-1" as the current task

  Scenario: A close spelled as a status change is detected
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd update ab-1 --status closed"
    Then attribution is written to "ab-1"

  Scenario: A task claimed and closed in quick succession is not recorded as free
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    And a ccusage reading was already cached for this session
    When the Bash watcher observes "bd close ab-1"
    Then the attribution records a cost estimate of 2.5

  Scenario: Closing an issue this session never claimed records no cost
    When the Bash watcher observes "bd close ab-99"
    Then no cost estimate is written to "ab-99"

  Scenario: A cost that cannot be read is omitted rather than written as zero
    Given ccusage cannot be read
    When the Bash watcher observes "bd close ab-1"
    Then attribution is written to "ab-1"
    And the attribution records the cost basis as unavailable
    And the attribution records no cost estimate
    And the attribution records a duration

  Scenario: A claim and a close in one command line are handled in order
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    When the Bash watcher observes "bd update ab-2 --claim && bd close ab-2"
    Then attribution is written to "ab-2"

  Scenario: The watcher exits with code 0 when the metadata write fails
    Given ccusage reports a cumulative session cost of 12.5 over 4000 tokens
    And the bd update command exits non-zero
    When the Bash watcher observes "bd close ab-1"
    Then the hook exits with code 0
