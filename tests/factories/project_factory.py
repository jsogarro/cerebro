"""
Project factory for generating test research project data.

Factories produce SQLAlchemy ORM instances aligned with the current
``src.models.db.research_project`` and ``src.models.db.research_result``
schemas. Build them with ``Factory(...)`` and ``session.add()`` them, then
commit.
"""

import json
import uuid

from factory import Factory, LazyAttribute, LazyFunction
from factory.fuzzy import FuzzyChoice, FuzzyFloat
from faker import Faker

from src.models.db.research_project import ProjectStatus, ResearchProject
from src.models.db.research_result import ResearchResult, ResultType

fake = Faker()


class ResearchProjectFactory(Factory):
    """Factory for creating test research projects."""

    class Meta:
        model = ResearchProject

    id = LazyFunction(uuid.uuid4)
    title = LazyAttribute(lambda o: f"Research: {fake.catch_phrase()}")
    query = LazyFunction(
        lambda: json.dumps(
            {
                "text": fake.paragraph(nb_sentences=3),
                "domains": ["AI", "ML"],
                "depth_level": "comprehensive",
            }
        )
    )
    domains = LazyFunction(
        lambda: list(
            fake.random_elements(
                elements=["AI", "ML", "Ethics", "Biology", "Physics"],
                length=3,
                unique=True,
            )
        )
    )
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
    result_type = FuzzyChoice([t.value for t in ResultType])
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
