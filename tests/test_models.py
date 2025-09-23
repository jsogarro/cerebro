"""
Tests for Research Platform models following TDD principles.
First test: Research Project model
"""

from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.models.research_project import (
    AgentTask,
    ResearchPlan,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
    ResearchStatus,
)


class TestResearchProjectModel:
    """Test Research Project model functionality."""

    def test_create_research_project(self):
        """Test creating a basic research project."""
        # Arrange
        query = ResearchQuery(
            text="What are the latest advances in quantum computing for drug discovery?",
            domains=["quantum computing", "pharmaceutical", "drug discovery"],
            depth_level="comprehensive",
        )

        # Act
        project = ResearchProject(
            title="Quantum Computing in Drug Discovery",
            query=query,
            user_id="user123",
        )

        # Assert
        assert project.id is not None
        assert isinstance(project.id, UUID)
        assert project.title == "Quantum Computing in Drug Discovery"
        assert project.query == query
        assert project.user_id == "user123"
        assert project.status == ResearchStatus.PENDING
        assert isinstance(project.created_at, datetime)
        assert project.updated_at is not None

    def test_research_scope_configuration(self):
        """Test research scope configuration."""
        # Arrange
        scope = ResearchScope(
            depth_level="exhaustive",
            domains=["AI", "Machine Learning", "Ethics"],
            time_period_start=datetime(2020, 1, 1),
            time_period_end=datetime(2024, 12, 31),
            languages=["en", "es", "fr"],
            geographic_scope=["North America", "Europe"],
            max_sources=100,
        )

        # Assert
        assert scope.depth_level == "exhaustive"
        assert len(scope.domains) == 3
        assert scope.time_period_start.year == 2020
        assert scope.time_period_end.year == 2024
        assert "en" in scope.languages
        assert scope.max_sources == 100

    def test_research_plan_generation(self):
        """Test research plan with agent task allocation."""
        # Arrange
        tasks = [
            AgentTask(
                agent_type="literature_review",
                description="Conduct systematic literature review on quantum computing applications",
                priority=1,
                estimated_duration_seconds=1800,
            ),
            AgentTask(
                agent_type="comparative_analysis",
                description="Compare different quantum computing approaches for molecular simulation",
                priority=2,
                estimated_duration_seconds=2400,
            ),
            AgentTask(
                agent_type="synthesis",
                description="Synthesize findings into coherent research narrative",
                priority=3,
                estimated_duration_seconds=1200,
            ),
        ]

        plan = ResearchPlan(
            methodology="systematic_review",
            tasks=tasks,
            estimated_total_duration_seconds=5400,
        )

        # Assert
        assert plan.methodology == "systematic_review"
        assert len(plan.tasks) == 3
        assert plan.tasks[0].agent_type == "literature_review"
        assert plan.estimated_total_duration_seconds == 5400

    def test_research_project_status_transitions(self):
        """Test research project status transitions."""
        # Arrange
        query = ResearchQuery(
            text="Climate change impact on global food security",
            domains=["climate science", "agriculture", "food security"],
        )
        project = ResearchProject(
            title="Climate Change and Food Security",
            query=query,
            user_id="user456",
        )

        # Act & Assert - Initial status
        assert project.status == ResearchStatus.PENDING

        # Start planning
        project.start_planning()
        assert project.status == ResearchStatus.PLANNING

        # Start research
        project.start_research()
        assert project.status == ResearchStatus.IN_PROGRESS

        # Complete research
        project.complete()
        assert project.status == ResearchStatus.COMPLETED
        assert project.completed_at is not None

    def test_research_project_with_plan(self):
        """Test attaching a research plan to a project."""
        # Arrange
        query = ResearchQuery(
            text="Impact of AI on employment",
            domains=["artificial intelligence", "economics", "labor market"],
        )
        project = ResearchProject(
            title="AI and Employment",
            query=query,
            user_id="user789",
        )

        tasks = [
            AgentTask(
                agent_type="literature_review",
                description="Review economic literature on technological unemployment",
                priority=1,
            ),
        ]
        plan = ResearchPlan(
            methodology="mixed_methods",
            tasks=tasks,
        )

        # Act
        project.set_plan(plan)

        # Assert
        assert project.plan == plan
        assert project.plan.methodology == "mixed_methods"
        assert len(project.plan.tasks) == 1

    def test_research_project_validation(self):
        """Test research project validation rules."""
        # Test empty query text
        with pytest.raises(
            ValidationError, match="String should have at least 1 character"
        ):
            query = ResearchQuery(text="", domains=["test"])

        # Test invalid depth level
        with pytest.raises(ValidationError, match="Invalid depth level"):
            query = ResearchQuery(
                text="Test query",
                domains=["test"],
                depth_level="invalid_level",
            )

        # Test empty domains
        with pytest.raises(ValidationError, match="List should have at least 1 item"):
            query = ResearchQuery(text="Test query", domains=[])

    def test_agent_task_status_tracking(self):
        """Test agent task status tracking."""
        # Arrange
        task = AgentTask(
            agent_type="literature_review",
            description="Test task",
            priority=1,
        )

        # Assert initial status
        assert task.status == "pending"
        assert task.started_at is None
        assert task.completed_at is None

        # Start task
        task.start()
        assert task.status == "in_progress"
        assert task.started_at is not None

        # Complete task
        task.complete()
        assert task.status == "completed"
        assert task.completed_at is not None

        # Fail task
        task2 = AgentTask(
            agent_type="analysis",
            description="Test task 2",
            priority=2,
        )
        task2.fail("Error occurred")
        assert task2.status == "failed"
        assert task2.error_message == "Error occurred"
