"""What a report is built from, and what is known about it.

A report's input is either a tenant/owner-scoped project's stored results or
content the caller supplied directly. The projection names which, so the
report cannot present user-supplied or unevaluated content as a verified run.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.claims.run_citations import RunCitations
from src.models.db.research_project import ProjectStatus, ResearchProject
from src.models.db.research_result import ResearchResult


class SourceKind(StrEnum):
    PROJECT_RESULTS = "project_results"
    USER_SUPPLIED = "user_supplied"


class EvaluationAvailability(StrEnum):
    # Claim-support evaluation has no passes to read yet, so the only truthful
    # state is that none was attempted; "passed" must never be a default.
    NOT_ATTEMPTED = "not_attempted"
    UNAVAILABLE = "unavailable"


class ReportSourceUnavailableError(Exception):
    """The project exists for the caller but has nothing reportable yet."""


@dataclass(frozen=True)
class ReportSourceProjection:
    source_kind: SourceKind
    evaluation: EvaluationAvailability
    input_data: dict[str, Any]

    @classmethod
    def user_supplied(cls, data: dict[str, Any]) -> "ReportSourceProjection":
        # Scores the caller typed in are claims, not evaluations; keeping them
        # would print an unmeasured 99% as if the platform had measured it.
        results = dict(data.get("aggregated_results", {}))
        results.pop("confidence_score", None)
        content = {k: v for k, v in data.items() if k != "quality_report"}
        content["aggregated_results"] = results
        return cls(
            SourceKind.USER_SUPPLIED, EvaluationAvailability.UNAVAILABLE, content
        )

    def as_input_data(self) -> dict[str, Any]:
        return {
            **self.input_data,
            "source_kind": self.source_kind.value,
            "evaluation_availability": self.evaluation.value,
        }


def run_citation_input(run_id: str, bound: RunCitations) -> dict[str, Any]:
    """Report input for citations bound to a run: minted ones and every gap.

    Records that were not minted are not citations; they appear as gaps so the
    report can say why a claim carries none.
    """
    minted = []
    for record in bound.minted:
        evidence = bound.evidence[record.evidence_id or ""]
        year = record.metadata.year.value if record.metadata.year else None
        minted.append(
            {
                "citation_id": record.citation_id,
                "claim_id": record.claim_id,
                "evidence_id": evidence.evidence_id,
                "evidence_sha256": evidence.content_sha256,
                "locator": evidence.locator,
                "source_uri": evidence.source_uri,
                "status": record.status.value,
                "support": record.support.value if record.support else None,
                "in_text": record.in_text,
                "missing_fields": list(record.missing_fields),
                "authors": [a.value for a in record.metadata.authors],
                "year": year,
                "doi": record.metadata.doi.value if record.metadata.doi else None,
            }
        )
    gaps = [
        {
            "reason": gap.reason.value,
            "claim_id": gap.claim_id,
            "evidence_id": gap.evidence_id,
            "field": gap.field.value if gap.field else None,
            "detail": gap.detail,
        }
        for gap in bound.result.gaps
    ]
    return {
        "citation_binding": "run_bound",
        "citation_run_id": run_id,
        "bound_citations": minted,
        "citation_gaps": gaps,
    }


def _finding_text(content: dict[str, Any]) -> str:
    return str(content.get("text") or content.get("finding") or "")


def _aggregate(
    rows: list[tuple[str, dict[str, Any], float | None]],
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "sources": [],
        "citations": [],
        "findings": {},
        "insights": [],
        "recommendations": [],
        "methodologies": {},
        "comparisons": {},
    }
    for result_type, content, confidence in rows:
        if result_type == "finding":
            category = str(content.get("category") or "findings")
            finding: dict[str, Any] = {"text": _finding_text(content)}
            if confidence is not None:
                finding["confidence"] = confidence
            results["findings"].setdefault(category, []).append(finding)
        elif result_type == "source":
            results["sources"].append(content)
        elif result_type == "citation":
            results["citations"].append(content)
        elif result_type == "methodology":
            results["methodologies"].update(content)
        elif result_type == "comparison":
            results["comparisons"].update(content)
        elif result_type in ("synthesis", "summary"):
            text = _finding_text(content)
            if text:
                results["insights"].append(text)
            results["recommendations"].extend(content.get("recommendations", []))
    confidences = [c for _, _, c in rows if c is not None]
    if confidences:
        results["confidence_score"] = sum(confidences) / len(confidences)
    return results


def _visible_to(
    project_id: UUID, user_id: UUID, organization_id: UUID
) -> tuple[Any, ...]:
    return (
        ResearchProject.id == project_id,
        ResearchProject.user_id == user_id,
        ResearchProject.organization_id == organization_id,
        ResearchProject.deleted_at.is_(None),
    )


def _require_reportable(status: ProjectStatus, has_results: bool) -> None:
    if status != ProjectStatus.COMPLETED:
        raise ReportSourceUnavailableError(
            f"Project is {status.value}; results are not final"
        )
    if not has_results:
        raise ReportSourceUnavailableError("Project has no stored results")


class ReportSourceRepository:
    """Reads a report's source from one project, scoped to its owner."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_available(
        self, project_id: UUID, *, user_id: UUID, organization_id: UUID
    ) -> bool:
        """Cheap pre-flight for the route: False when the caller cannot see the
        project; raises when it is theirs but has nothing final to report."""
        state = (
            await self.session.execute(
                select(
                    ResearchProject.status,
                    exists().where(
                        ResearchResult.project_id == ResearchProject.id,
                        ResearchResult.deleted_at.is_(None),
                    ),
                ).where(*_visible_to(project_id, user_id, organization_id))
            )
        ).one_or_none()
        if state is None:
            return False
        status, has_results = state
        _require_reportable(status, has_results)
        return True

    async def load_project_source(
        self, project_id: UUID, *, user_id: UUID, organization_id: UUID
    ) -> ReportSourceProjection | None:
        """Return the projection, or None when the caller cannot see the project.

        Missing, deleted, other-owner and other-tenant projects are all None so
        the caller cannot tell them apart.
        """
        project = (
            await self.session.execute(
                select(
                    ResearchProject.title,
                    ResearchProject.query,
                    ResearchProject.domains,
                    ResearchProject.status,
                    ResearchProject.quality_score,
                    ResearchProject.workflow_id,
                ).where(*_visible_to(project_id, user_id, organization_id))
            )
        ).one_or_none()
        if project is None:
            return None
        if project.status != ProjectStatus.COMPLETED:
            _require_reportable(project.status, True)

        rows = (
            await self.session.execute(
                select(
                    ResearchResult.result_type,
                    ResearchResult.content,
                    ResearchResult.confidence_score,
                )
                .where(
                    ResearchResult.project_id == project_id,
                    ResearchResult.deleted_at.is_(None),
                )
                .order_by(ResearchResult.created_at, ResearchResult.id)
            )
        ).all()
        _require_reportable(project.status, bool(rows))

        return ReportSourceProjection(
            source_kind=SourceKind.PROJECT_RESULTS,
            evaluation=EvaluationAvailability.NOT_ATTEMPTED,
            input_data={
                "title": project.title,
                "query": project.query,
                "domains": list(project.domains or []),
                "project_id": str(project_id),
                "aggregated_results": _aggregate([(t, c, s) for t, c, s in rows]),
                "quality_report": (
                    {"quality_score": project.quality_score}
                    if project.quality_score is not None
                    else {}
                ),
                "metadata": {"workflow_id": project.workflow_id},
            },
        )
