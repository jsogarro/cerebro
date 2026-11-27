"""Rows the evaluation-results CHECKs must reject, shared by the SQLite schema
test and the migrated-Postgres test so the ORM and the migration are held to
one list rather than two that can drift."""

from datetime import UTC, datetime
from typing import Any

NOW = datetime(2026, 11, 27, tzinfo=UTC)
ORG_ID = "00000000-0000-0000-0000-0000000000aa"


def valid_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "evaluation_result_id": "evaluation-1",
        "run_id": "run-1",
        "organization_id": ORG_ID,
        "evaluator_id": "claims.coverage",
        "evaluator_version": "1.0.0",
        "dimension": "claims.coverage",
        "status": "passed",
        "deterministic": True,
        "details": {},
        "trigger": "trigger-1",
        "completed_at": NOW,
    }
    row.update(overrides)
    return row


# (constraint name, overrides that violate exactly that constraint)
VIOLATIONS: list[tuple[str, dict[str, Any]]] = [
    ("ck_agent_evaluation_result_status", {"status": "not_attempted"}),
    (
        "ck_agent_evaluation_result_non_verdict_requires_reason",
        {"status": "error", "error_code": None},
    ),
    (
        "ck_agent_evaluation_result_non_verdict_requires_reason",
        {"status": "abstained", "error_code": ""},
    ),
    ("ck_agent_evaluation_result_score_range", {"score": 1.5}),
    ("ck_agent_evaluation_result_score_range", {"score": -0.1}),
    (
        "ck_agent_evaluation_result_deterministic_has_no_judge",
        {"judge_model_id": "j", "rubric_sha256": "a" * 64},
    ),
    (
        "ck_agent_evaluation_result_judge_fields_together",
        {"deterministic": False, "judge_model_id": "j"},
    ),
    ("ck_agent_evaluation_result_trigger_non_empty", {"trigger": ""}),
]
