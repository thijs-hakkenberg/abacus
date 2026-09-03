"""Binds every scenario in ``features/`` to the step definitions.

``scenarios()`` collects *all* scenarios in each file, which is the property that
makes the feature space executable rather than decorative: adding a scenario to a
``.feature`` adds a test, and if no step definition matches its wording the suite
fails with ``StepDefinitionNotFoundError`` rather than quietly skipping it — the
drift that a hand-synced feature directory has no guard against.

The step modules are imported for their side effect — pytest-bdd registers steps
at import time and resolves them from this module's namespace.
"""

from pytest_bdd import scenarios

from audit_steps import *  # noqa: F401,F403
from auto_init_steps import *  # noqa: F401,F403
from commit_steps import *  # noqa: F401,F403
from common_steps import *  # noqa: F401,F403
from consent_steps import *  # noqa: F401,F403
from primer_steps import *  # noqa: F401,F403
from when_steps import *  # noqa: F401,F403
from world import world  # noqa: F401

scenarios("gate-edit-enforcement.feature")
scenarios("task-claim-snapshot.feature")
scenarios("task-close-attribution.feature")
scenarios("session-lifecycle-priming.feature")
scenarios("session-end-finalization.feature")
scenarios("cost-estimate-labelling.feature")
scenarios("workspace-auto-init.feature")
scenarios("task-audit.feature")
scenarios("consent-acknowledgement.feature")
scenarios("commit-capture.feature")
