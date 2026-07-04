"""
Project factory for generating test research project data.

Factories produce SQLAlchemy ORM instances aligned with the current
``src.models.db.research_project`` and ``src.models.db.research_result``
schemas. Build them with ``Factory(...)`` and ``session.add()`` them, then
commit.

Conventions:

- ``id`` and ``project_id`` always produce ``uuid.UUID`` instances to
  match the ``UUID`` portable type on ``BaseModel``.
- Enum-backed columns (e.g. ``ResearchProject.status``) receive enum
  members directly so SQLAlchemy serializes them via ``SQLEnum``.
- String columns that store enum *values* (e.g. ``ResearchResult.result_type``
  is ``Mapped[str]`` not ``Mapped[ResultType]``) receive the ``.value``
  of an enum case so the type matches the column.
- JSON columns receive native Python ``list``/``dict`` (not JSON strings);
  ``Text`` columns like ``ResearchProject.query`` receive serialized JSON.
"""

import json
import uuid

from factory import Factory, LazyAttribute, LazyFunction
from factory.fuzzy import FuzzyChoice, FuzzyFloat
from faker import Faker

from src.models.db.research_project import ProjectStatus, ResearchProject
from src.models.db.research_result import ResearchResult, ResultType

fake = Faker()


def _build_query_payload() -> str:
    """Build a realistic JSON-serialized query for ``ResearchProject.query``.

    The shape mirrors what API handlers store via ``json.dumps`` of a
    ``ResearchQuery`` model. ``domains`` here is just one valid value for the
    JSON ``domains`` key embedded in the query payload; it intentionally
    differs from the top-level ``ResearchProject.domains`` JSON column,
    which is populated separately by the factory below.
    """
    return json.dumps(
        {
            "text": fake.paragraph(nb_sentences=3),
            "domains": ["AI", "ML"],
            "depth_level": "comprehensive",
        }
    )


class ResearchProjectFactory(Factory):
    """Factory for creating test research projects."""

    class Meta:
        model = ResearchProject

    id = LazyFunction(uuid.uuid4)
    title = LazyAttribute(lambda o: f"Research: {fake.catch_phrase()}")
    # ``query`` is a Text column holding a JSON-serialized payload.
    query = LazyFunction(_build_query_payload)
    # ``domains`` is a JSON column holding a list of strings (the
    # top-level project domains, separate from the nested domains key
    # inside the query payload above).
    domains = LazyFunction(
        lambda: list(
            fake.random_elements(
                elements=["AI", "ML", "Ethics", "Biology", "Physics"],
                length=3,
                unique=True,
            )
        )
    )
    # ``status`` is a SQLEnum column — pass enum members.
    status = FuzzyChoice(list(ProjectStatus))
    quality_score = FuzzyFloat(0.0, 1.0)
    user_id = LazyFunction(lambda: str(uuid.uuid4()))
    organization_id = None
    workflow_id = LazyFunction(lambda: f"workflow-{uuid.uuid4()}")
    project_metadata = LazyFunction(dict)


class ResearchResultFactory(Factory):
    """Factory for creating test research results."""

    class Meta:
        model = ResearchResult

    id = LazyFunction(uuid.uuid4)
    project_id = LazyFunction(uuid.uuid4)
    # ``result_type`` is ``Mapped[str]`` (not ``Mapped[ResultType]``) —
    # pass enum *values*, not enum members.
    result_type = FuzzyChoice([t.value for t in ResultType])
    # ``content`` is a JSON column — native dict, not a JSON string.
    content = LazyFunction(
        lambda: {
            "summary": fake.paragraph(nb_sentences=5),
            "findings": [fake.sentence() for _ in range(3)],
        }
    )
    confidence_score = FuzzyFloat(0.7, 1.0)
    agent_type = FuzzyChoice(
        [
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation",
        ]
    )
    result_metadata = LazyFunction(dict)
    source_id = None
