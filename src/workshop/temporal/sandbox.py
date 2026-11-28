"""The workflow sandbox every worker uses (ADR-012).

pydantic-ai's py-key-value dependency installs a beartype import hook when the
pipeline is imported. Inside the sandbox that hook would re-import beartype
and fail on a circular import, so beartype is passed through from the host.
"""

from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

WORKFLOW_RUNNER = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules("beartype")
)
