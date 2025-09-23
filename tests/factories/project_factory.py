"""
Project factory for generating test research project data.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from factory import DictFactory, Factory, LazyAttribute, LazyFunction
from factory.fuzzy import FuzzyChoice, FuzzyDateTime, FuzzyInteger
from faker import Faker

from src.models.db.agent_task import AgentTask
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult

fake = Faker()


class ResearchQueryFactory(DictFactory):
    """Factory for creating research query data."""

    text = LazyFunction(lambda: fake.paragraph(nb_sentences=3))
    domains = LazyFunction(
        lambda: fake.random_elements(
            elements=[
                "Artificial Intelligence",
                "Machine Learning",
                "Ethics",
                "Biology",
                "Physics",
                "Economics",
                "Psychology",
                "Neuroscience",
                "Computer Science",
                "Mathematics",
            ],
            length=3,
            unique=True,
        )
    )
    depth_level = FuzzyChoice(["basic", "intermediate", "comprehensive"])
    keywords = LazyFunction(lambda: [fake.word() for _ in range(fake.random_int(3, 8))])
    time_range = LazyFunction(
        lambda: {
            "start": (datetime.utcnow() - timedelta(days=365 * 5)).isoformat(),
            "end": datetime.utcnow().isoformat(),
        }
    )
    sources = LazyFunction(
        lambda: fake.random_elements(
            elements=["academic", "industry", "government", "nonprofit"],
            length=2,
            unique=True,
        )
    )


class ResearchProjectFactory(Factory):
    """Factory for creating test research projects."""

    class Meta:
        model = ResearchProject

    id = LazyFunction(lambda: str(uuid.uuid4()))
    title = LazyAttribute(lambda o: f"Research: {fake.catch_phrase()}")
    description = LazyFunction(lambda: fake.text(max_nb_chars=500))
    user_id = LazyFunction(lambda: str(uuid.uuid4()))
    status = FuzzyChoice(["pending", "in_progress", "completed", "failed", "cancelled"])
    query_text = LazyFunction(lambda: fake.paragraph(nb_sentences=3))
    domains = LazyFunction(
        lambda: json.dumps(
            fake.random_elements(
                elements=["AI", "ML", "Ethics", "Biology", "Physics"],
                length=3,
                unique=True,
            )
        )
    )
    depth_level = FuzzyChoice(["basic", "intermediate", "comprehensive"])
    workflow_id = LazyFunction(lambda: f"workflow-{uuid.uuid4()}")
    progress = FuzzyInteger(0, 100)
    created_at = FuzzyDateTime(
        start_dt=datetime.utcnow() - timedelta(days=90), end_dt=datetime.utcnow()
    )
    updated_at = LazyAttribute(lambda o: o.created_at + timedelta(hours=1))
    completed_at = LazyAttribute(
        lambda o: (
            o.created_at + timedelta(hours=fake.random_int(2, 48))
            if o.status == "completed"
            else None
        )
    )

    @classmethod
    def create_pending(cls, **kwargs) -> ResearchProject:
        """Create a pending research project."""
        defaults = {
            "status": "pending",
            "progress": 0,
            "workflow_id": None,
            "completed_at": None,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_in_progress(cls, **kwargs) -> ResearchProject:
        """Create an in-progress research project."""
        defaults = {
            "status": "in_progress",
            "progress": fake.random_int(10, 90),
            "workflow_id": f"workflow-{uuid.uuid4()}",
            "completed_at": None,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_completed(cls, **kwargs) -> ResearchProject:
        """Create a completed research project."""
        created = datetime.utcnow() - timedelta(days=fake.random_int(1, 30))
        defaults = {
            "status": "completed",
            "progress": 100,
            "workflow_id": f"workflow-{uuid.uuid4()}",
            "created_at": created,
            "completed_at": created + timedelta(hours=fake.random_int(2, 24)),
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_failed(cls, **kwargs) -> ResearchProject:
        """Create a failed research project."""
        defaults = {
            "status": "failed",
            "progress": fake.random_int(10, 80),
            "workflow_id": f"workflow-{uuid.uuid4()}",
            "error_message": fake.sentence(),
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_with_query(cls, query: dict[str, Any], **kwargs) -> ResearchProject:
        """Create a research project with specific query."""
        defaults = {
            "query_text": query.get("text", fake.paragraph()),
            "domains": json.dumps(query.get("domains", ["AI", "ML"])),
            "depth_level": query.get("depth_level", "comprehensive"),
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_batch_for_user(
        cls, user_id: str, count: int = 5
    ) -> list[ResearchProject]:
        """Create multiple projects for a user."""
        projects = []
        statuses = ["pending", "in_progress", "completed", "failed", "cancelled"]

        for i in range(count):
            status = statuses[i % len(statuses)]
            if status == "pending":
                project = cls.create_pending(user_id=user_id)
            elif status == "in_progress":
                project = cls.create_in_progress(user_id=user_id)
            elif status == "completed":
                project = cls.create_completed(user_id=user_id)
            elif status == "failed":
                project = cls.create_failed(user_id=user_id)
            else:
                project = cls(user_id=user_id, status=status)

            projects.append(project)

        return projects


class ResearchResultFactory(Factory):
    """Factory for creating test research results."""

    class Meta:
        model = ResearchResult

    id = LazyFunction(lambda: str(uuid.uuid4()))
    project_id = LazyFunction(lambda: str(uuid.uuid4()))
    agent_name = FuzzyChoice(
        [
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation",
        ]
    )
    result_type = FuzzyChoice(["analysis", "summary", "recommendation", "citation"])
    content = LazyFunction(
        lambda: json.dumps(
            {
                "summary": fake.paragraph(nb_sentences=5),
                "findings": [fake.sentence() for _ in range(3)],
                "recommendations": [fake.sentence() for _ in range(2)],
                "citations": [
                    {
                        "title": fake.catch_phrase(),
                        "authors": [fake.name() for _ in range(fake.random_int(1, 3))],
                        "year": fake.random_int(2015, 2024),
                        "source": fake.company(),
                    }
                    for _ in range(fake.random_int(3, 10))
                ],
                "confidence_score": round(fake.random.uniform(0.7, 1.0), 2),
                "metadata": {
                    "sources_analyzed": fake.random_int(10, 100),
                    "processing_time": round(fake.random.uniform(1.0, 10.0), 2),
                },
            }
        )
    )
    confidence_score = LazyFunction(lambda: round(fake.random.uniform(0.7, 1.0), 2))
    created_at = FuzzyDateTime(
        start_dt=datetime.utcnow() - timedelta(days=30), end_dt=datetime.utcnow()
    )

    @classmethod
    def create_for_project(cls, project: ResearchProject, **kwargs) -> "ResearchResult":
        """Create a result for a specific project."""
        defaults = {"project_id": project.id}
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_literature_review(cls, **kwargs) -> "ResearchResult":
        """Create a literature review result."""
        content = {
            "papers_reviewed": fake.random_int(20, 100),
            "key_themes": [fake.word() for _ in range(5)],
            "summary": fake.paragraph(nb_sentences=10),
            "gaps_identified": [fake.sentence() for _ in range(3)],
            "citations": [
                {
                    "title": fake.catch_phrase(),
                    "authors": [fake.name() for _ in range(2)],
                    "year": fake.random_int(2015, 2024),
                    "journal": fake.company(),
                    "doi": f"10.{fake.random_int(1000, 9999)}/{fake.word()}",
                }
                for _ in range(10)
            ],
        }
        defaults = {
            "agent_name": "literature_review",
            "result_type": "analysis",
            "content": json.dumps(content),
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_synthesis(cls, **kwargs) -> "ResearchResult":
        """Create a synthesis result."""
        content = {
            "integrated_findings": fake.paragraph(nb_sentences=15),
            "coherent_narrative": fake.text(max_nb_chars=1000),
            "key_insights": [fake.sentence() for _ in range(5)],
            "conclusions": [fake.sentence() for _ in range(3)],
            "future_directions": [fake.sentence() for _ in range(3)],
        }
        defaults = {
            "agent_name": "synthesis",
            "result_type": "summary",
            "content": json.dumps(content),
        }
        defaults.update(kwargs)
        return cls(**defaults)


class AgentTaskFactory(Factory):
    """Factory for creating test agent tasks."""

    class Meta:
        model = AgentTask

    id = LazyFunction(lambda: str(uuid.uuid4()))
    project_id = LazyFunction(lambda: str(uuid.uuid4()))
    agent_name = FuzzyChoice(
        [
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation",
        ]
    )
    status = FuzzyChoice(["pending", "running", "completed", "failed", "cancelled"])
    input_data = LazyFunction(
        lambda: json.dumps(
            {
                "query": fake.paragraph(),
                "domains": ["AI", "ML"],
                "parameters": {
                    "max_results": fake.random_int(10, 100),
                    "depth": "comprehensive",
                },
            }
        )
    )
    output_data = LazyAttribute(
        lambda o: (
            json.dumps(
                {
                    "result": fake.paragraph(nb_sentences=5),
                    "metadata": {"execution_time": fake.random.uniform(1.0, 10.0)},
                }
            )
            if o.status == "completed"
            else None
        )
    )
    error_message = LazyAttribute(
        lambda o: fake.sentence() if o.status == "failed" else None
    )
    started_at = LazyFunction(datetime.utcnow)
    completed_at = LazyAttribute(
        lambda o: (
            o.started_at + timedelta(seconds=fake.random_int(10, 300))
            if o.status in ["completed", "failed"]
            else None
        )
    )
    created_at = LazyFunction(datetime.utcnow)

    @classmethod
    def create_pending(cls, **kwargs) -> AgentTask:
        """Create a pending agent task."""
        defaults = {
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "output_data": None,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_running(cls, **kwargs) -> AgentTask:
        """Create a running agent task."""
        defaults = {
            "status": "running",
            "started_at": datetime.utcnow(),
            "completed_at": None,
            "output_data": None,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_completed(cls, **kwargs) -> AgentTask:
        """Create a completed agent task."""
        started = datetime.utcnow() - timedelta(minutes=5)
        defaults = {
            "status": "completed",
            "started_at": started,
            "completed_at": started + timedelta(seconds=fake.random_int(30, 180)),
            "error_message": None,
        }
        defaults.update(kwargs)
        return cls(**defaults)


class TestProjectScenarioGenerator:
    """Generate realistic test project scenarios."""

    @staticmethod
    def create_project_with_full_history(user_id: str) -> dict[str, Any]:
        """Create a project with complete execution history."""
        project = ResearchProjectFactory.create_completed(user_id=user_id)

        # Create agent tasks for each agent
        agents = [
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation",
        ]

        tasks = []
        results = []

        for agent in agents:
            task = AgentTaskFactory.create_completed(
                project_id=project.id, agent_name=agent
            )
            tasks.append(task)

            # Create corresponding result
            if agent == "literature_review":
                result = ResearchResultFactory.create_literature_review(
                    project_id=project.id
                )
            elif agent == "synthesis":
                result = ResearchResultFactory.create_synthesis(project_id=project.id)
            else:
                result = ResearchResultFactory(project_id=project.id, agent_name=agent)
            results.append(result)

        return {
            "project": project,
            "tasks": tasks,
            "results": results,
        }

    @staticmethod
    def create_failed_project_scenario(user_id: str) -> dict[str, Any]:
        """Create a project that failed during execution."""
        project = ResearchProjectFactory.create_failed(
            user_id=user_id,
            error_message="Agent execution failed: Timeout during literature review",
        )

        # Create some successful tasks and one failed
        tasks = [
            AgentTaskFactory.create_completed(
                project_id=project.id, agent_name="literature_review"
            ),
            AgentTaskFactory.create_failed(
                project_id=project.id,
                agent_name="comparative_analysis",
                error_message="Timeout: Agent took too long to respond",
            ),
        ]

        # Only one result for the completed task
        results = [
            ResearchResultFactory.create_literature_review(project_id=project.id)
        ]

        return {
            "project": project,
            "tasks": tasks,
            "results": results,
        }

    @staticmethod
    def create_organization_projects(
        org_users: list[Any], projects_per_user: int = 3
    ) -> list[ResearchProject]:
        """Create projects for an organization's users."""
        all_projects = []

        for user in org_users:
            projects = ResearchProjectFactory.create_batch_for_user(
                user.id, count=projects_per_user
            )
            all_projects.extend(projects)

        return all_projects
