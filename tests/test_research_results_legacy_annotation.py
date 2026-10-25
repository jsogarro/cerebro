"""The research results read path must flag pre-fix fabricated verification.

Rows persisted before the fabricating code was removed (a ``"Mock Publisher"``
DOI resolution, an unconditional ``originality_score``) still sit verbatim in
``research_results.content``. Deleting the code that produced them retracts
nothing a caller reads, so the endpoint that serves those rows must annotate
them. ``src.qa.legacy_annotation`` existed with no caller on any read path.
"""

from types import SimpleNamespace
from uuid import uuid4

from src.api.routes import research
from src.middleware.tenant_context import TenantContext
from src.models.db.research_result import ResearchResult
from src.qa.legacy_annotation import ANNOTATION_KEY, ANNOTATION_REASON_KEY


class _Repo:
    def __init__(self, results: list[ResearchResult]) -> None:
        self.project = SimpleNamespace(results=results)

    async def get_for_user(self, *args: object, **kwargs: object) -> SimpleNamespace:
        return self.project


def _row(content: dict[str, object]) -> ResearchResult:
    return ResearchResult(
        project_id=uuid4(),
        result_type="citation",
        content=content,
        result_metadata={},
    )


async def test_legacy_fabricated_row_is_served_annotated() -> None:
    legacy = _row(
        {
            "doi_resolution": {
                "10.1234/x": {
                    "resolved": True,
                    "crossref_data": {"publisher": "Mock Publisher"},
                }
            }
        }
    )
    clean = _row({"formatted_citations": ["Doe, J. (2020). A paper."]})

    response = await research.get_research_results(
        project_id=uuid4(),
        repo=_Repo([legacy, clean]),
        tenant_context=TenantContext(user_id="user-1", organization_id="org-1"),
        execution_service=None,
    )

    served_legacy, served_clean = response["results"]
    assert served_legacy[ANNOTATION_KEY] is True
    assert served_legacy[ANNOTATION_REASON_KEY]
    assert served_legacy["content"] == legacy.content
    assert ANNOTATION_KEY not in served_clean
    assert served_clean["content"] == clean.content
    # The persisted row itself is never mutated.
    assert ANNOTATION_KEY not in legacy.content
