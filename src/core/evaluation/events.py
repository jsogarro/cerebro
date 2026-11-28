"""Identity and failure codes shared by every evaluator."""

import uuid
from typing import Final

EVALUATION_DESTINATION: Final = "evaluation"
EVALUATION_TRIGGER_EVENT: Final = "run.succeeded"
EVALUATOR_EXCEPTION_CODE: Final = "evaluator_exception"
EVALUATOR_CONTRACT_VIOLATION_CODE: Final = "evaluator_contract_violation"

_NAMESPACE: Final = uuid.UUID("5b0f5f0e-7a52-4c3e-9d5e-3b7c1e0a4f21")


def derive_evaluation_result_id(
    *,
    organization_id: uuid.UUID | str,
    run_id: str,
    evaluator_id: str,
    evaluator_version: str,
    dimension: str,
    artifact_id: str | None,
    discriminator: str = "",
) -> str:
    """Return a stable id so a redelivered evaluation is recognised, not repeated.

    The organization is part of it so one tenant can never pre-claim an id
    another tenant's dispatcher will derive.

    The version is part of the id on purpose: a new evaluator version is a new
    judgment, and must append rather than collide with the old one.
    """
    parts = (
        str(organization_id),
        run_id,
        evaluator_id,
        evaluator_version,
        dimension,
        artifact_id or "",
        discriminator,
    )
    return str(uuid.uuid5(_NAMESPACE, "\x1f".join(parts)))
