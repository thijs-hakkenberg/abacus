Feature: A project without a beads workspace can be given one automatically
  As a Claude Code user
  I want a new project to get its own beads workspace when I first open it there
  So that enforcement covers every project without me hand-initialising each one before I can edit anything

  Background:
    Given the working directory is not a beads workspace
    And the working directory is a git repository
    And no beads issue is in progress

  Scenario: Automatic initialisation is off unless it is asked for
    When the SessionStart hook runs
    Then no beads workspace is created

  Scenario: An enabled project is given a workspace and then primed
    Given automatic workspace initialisation is enabled for the whole machine
    When the SessionStart hook runs
    Then a beads workspace is created
    And the workspace is created without a prompt
    And the workspace is excluded from version control
    And the injected context names the two commands that satisfy the gate

  Scenario: A directory that is not a git repository is left alone
    Given automatic workspace initialisation is enabled for the whole machine
    And the working directory is not a git repository
    When the SessionStart hook runs
    Then no beads workspace is created
    And no context is injected

  Scenario: A project outside the configured roots is left alone
    Given automatic workspace initialisation is enabled only under a different root
    When the SessionStart hook runs
    Then no beads workspace is created

  Scenario: Compaction never creates a workspace
    Given automatic workspace initialisation is enabled for the whole machine
    When the PreCompact hook runs
    Then no beads workspace is created

  Scenario: An initialisation that cannot be read back is not treated as a workspace
    Given automatic workspace initialisation is enabled for the whole machine
    And the bd list command exits non-zero
    When the SessionStart hook runs
    Then no context is injected

  Scenario: Session start exits with code 0 when initialisation fails
    Given automatic workspace initialisation is enabled for the whole machine
    And the bd init command exits non-zero
    When the SessionStart hook runs
    Then the hook exits with code 0
    And no context is injected
