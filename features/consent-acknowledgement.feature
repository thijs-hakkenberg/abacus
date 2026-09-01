Feature: Nothing is governed until the governing settings are acknowledged
  As someone who has just installed abacus
  I want it to tell me what it would do and then wait
  So that no edit is refused, no workspace created and no issue written to before I have agreed to it

  Background:
    Given the working directory is a beads workspace
    And no beads issue is in progress
    And abacus has not been acknowledged

  Scenario: An untracked edit is allowed while nothing has been agreed to
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call
    And the gate prints nothing

  Scenario: The same untracked edit is denied once the settings are agreed to
    Given the governing settings have been acknowledged
    When the PreToolUse gate runs for an Edit
    Then the gate denies the tool call

  Scenario: Blocking projects without a workspace also waits to be agreed to
    Given the working directory is not a beads workspace
    And the gate is configured to block projects without a beads workspace
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call

  Scenario: An inert gate spawns no subprocess at all
    Given beads issue "ab-1" is in progress
    When the PreToolUse gate runs for an Edit
    Then beads is never invoked
    And ccusage is never invoked

  Scenario: Session start says what abacus would do instead of priming
    When the SessionStart hook runs
    Then the injected context says abacus is governing nothing
    And the injected context lists what agreeing would switch on
    And the injected context names the command that records agreement

  Scenario: The notice describes the settings actually configured
    Given automatic workspace initialisation is enabled only under a different root
    And session end is configured to push
    When the SessionStart hook runs
    Then the injected context names the directory a workspace could be created in
    And the injected context says a remote would be reached

  Scenario: No workspace is created in a repository nobody agreed to
    Given the working directory is not a beads workspace
    And the working directory is a git repository
    And automatic workspace initialisation is enabled for the whole machine
    When the SessionStart hook runs
    Then no beads workspace is created

  Scenario: The next prompt asks when the plugin was installed mid-session
    When the UserPromptSubmit hook runs
    Then the injected context names the command that records agreement

  Scenario: The question is asked once in a session, not on every prompt
    When the UserPromptSubmit hook runs
    Then the injected context names the command that records agreement
    When the UserPromptSubmit hook runs again
    Then no context is injected

  Scenario: The kill switch outranks the question
    Given the plugin is disabled by environment variable
    When the UserPromptSubmit hook runs
    Then no context is injected

  Scenario: Closing a task writes no attribution while nothing is agreed to
    Given the task "ab-1" is being tracked with a cost baseline of 1
    And ccusage reports a cumulative session cost of 4 over 5000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then no attribution is written

  Scenario: The same close writes attribution once the settings are agreed to
    Given the governing settings have been acknowledged
    And the task "ab-1" is being tracked with a cost baseline of 1
    And ccusage reports a cumulative session cost of 4 over 5000 tokens
    When the Bash watcher observes "bd close ab-1"
    Then attribution is written to "ab-1"

  Scenario: Repair on Stop writes nothing while nothing is agreed to
    Given the task "ab-1" is being tracked with a cost baseline of 1
    And beads issue "ab-1" has been closed outside this session
    And ccusage reports a cumulative session cost of 4 over 5000 tokens
    When the Stop hook runs
    Then no attribution is written

  Scenario: A session that ends unacknowledged records nothing and reaches no remote
    Given the task "ab-1" is being tracked with a cost baseline of 1
    And session end is configured to push
    And ccusage reports a cumulative session cost of 4 over 5000 tokens
    When the SessionEnd hook runs
    Then no attribution is written
    And beads is not asked to push

  Scenario: Agreeing switches governance on
    When the agreement is recorded
    And the PreToolUse gate runs for an Edit
    Then the gate denies the tool call

  Scenario: Reading the notice is not agreeing to it
    When the notice is shown without an answer
    And the PreToolUse gate runs for an Edit
    Then the gate allows the tool call

  Scenario: Withdrawing the agreement makes abacus inert again
    Given the governing settings have been acknowledged
    When the agreement is withdrawn
    And the PreToolUse gate runs for an Edit
    Then the gate allows the tool call

  Scenario: Widening where workspaces may be created asks again
    Given automatic workspace initialisation is enabled only under a different root
    And the governing settings have been acknowledged
    And automatic workspace initialisation is enabled for the whole machine
    When the PreToolUse gate runs for an Edit
    Then the gate allows the tool call
    When the notice is shown without an answer
    Then the notice names the setting that changed

  Scenario: Bumping the pinned ccusage version does not ask again
    Given the governing settings have been acknowledged
    And the pinned ccusage version is changed
    When the PreToolUse gate runs for an Edit
    Then the gate denies the tool call

  Scenario: An explicitly invoked repair still runs while nothing is agreed to
    Given the workspace holds a closed issue "ab-9" with no attribution
    When the audit runs with repairs enabled
    Then attribution is written to "ab-9"

  Scenario: The consent surface exits with code 0 when the agreement cannot be recorded
    Given the state directory cannot be written to
    When the agreement is recorded
    Then the hook exits with code 0
    And abacus is still not acknowledged
