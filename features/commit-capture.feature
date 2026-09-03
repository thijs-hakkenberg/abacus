Feature: Recording which commits a task produced
  As a Claude Code user
  I want the commits made while a task was claimed recorded against that task
  So that I can ask which commits a task produced and what each one cost, and never be
  told an edge that was merely guessed at

  No Claude Code hook fires on a git commit, so capture compares HEAD against a
  watermark recorded earlier in the session. Every edge carries the basis it was
  established on, and only the two bases that were witnessed are ever written: a
  `Beads-Task:` trailer git itself parsed, or a HEAD move observed while a task was
  claimed. A timestamp falling inside a claim window is not evidence and is never
  written (adr/015).

  Background:
    Given the working directory is a beads workspace
    And the working directory is a git repository
    And HEAD is at commit "dec0de"

  Scenario: The first sight of a repository seeds the watermark and writes nothing
    Given no watermark has been recorded for this repository
    And the task "ab-1" is being tracked with a cost baseline of 10.0
    When the Bash watcher observes "git commit -m 'do the work'"
    Then no commit edge is written
    And the watermark for this repository is recorded as "dec0de"

  Scenario: A commit made while a task was claimed is recorded as observed
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim
    When the Bash watcher observes "git commit -m 'do the work'"
    Then a commit edge for "c0ffee" is written to "ab-1" with basis "observed"
    And the watermark for this repository is recorded as "dec0de"

  Scenario: A commit declaring three tasks is recorded against all three
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim declaring "ab-7, ab-8, ab-9"
    When the Bash watcher observes "git commit -m 'close three'"
    Then a commit edge for "c0ffee" is written to "ab-7" with basis "declared"
    And a commit edge for "c0ffee" is written to "ab-8" with basis "declared"
    And a commit edge for "c0ffee" is written to "ab-9" with basis "declared"

  Scenario: A declared commit needs no claim at all
    Given no task is being tracked in session state
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim declaring "ab-7"
    When the Bash watcher observes "git commit -m 'close one'"
    Then a commit edge for "c0ffee" is written to "ab-7" with basis "declared"

  Scenario: A commit older than the claim was not observed being made during it
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made before the claim
    When the Bash watcher observes "git pull"
    Then no commit edge is written
    And the watermark for this repository is recorded as "dec0de"

  Scenario: A HEAD move larger than the cap is not one boundary's work
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And 51 commits were made after the claim
    When the Bash watcher observes "git rebase main"
    Then no commit edge is written
    And the watermark for this repository is recorded as "dec0de"

  Scenario: Switching branches re-marks the watermark without attributing anything
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim
    When the Bash watcher observes "git checkout other-branch"
    Then no commit edge is written
    And the watermark for this repository is recorded as "dec0de"

  Scenario: A commit command quoted inside a heredoc is documentation, not a boundary
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim
    When the Bash watcher observes a heredoc documenting "git commit -m 'do the work'"
    Then no commit edge is written
    And git is never invoked

  Scenario: A repository with no beads workspace is none of our business
    Given the working directory is not a beads workspace
    And the task "ab-1" is being tracked with a cost baseline of 10.0
    When the Bash watcher observes "git commit -m 'do the work'"
    Then no commit edge is written

  Scenario: A commit edge is never written before the settings are acknowledged
    Given abacus has not been acknowledged
    And the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim
    When the Bash watcher observes "git commit -m 'do the work'"
    Then no commit edge is written

  Scenario: A commit no verb list matched is collected by the Stop sweep
    Given the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    And a commit "c0ffee" was made after the claim
    When the Stop hook runs
    Then a commit edge for "c0ffee" is written to "ab-1" with basis "observed"

  Scenario: The watcher exits with code 0 when git is not on PATH
    Given the git executable is not on PATH
    And the task "ab-1" is being tracked with a cost baseline of 10.0
    And the watermark for this repository is "ba5e"
    When the Bash watcher observes "git commit -m 'do the work'"
    Then no commit edge is written
    And the hook exits with code 0
