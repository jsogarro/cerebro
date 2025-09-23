"""
Research Project models for the Research Platform.
Implements domain models following TDD principles.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class ResearchStatus(str, Enum):
    """Research project status enumeration."""

    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchDepth(str, Enum):
    """Research depth level enumeration."""

    SURVEY = "survey"
    COMPREHENSIVE = "comprehensive"
    EXHAUSTIVE = "exhaustive"


class ResearchQuery(BaseModel):
    """Model for research query input."""

    text: str = Field(..., min_length=1, description="The research question")
    domains: list[str] = Field(..., min_length=1, description="Research domains")
    depth_level: str = Field(default="comprehensive", description="Research depth")

    @field_validator("text")
    @classmethod
    def validate_query_text(cls, v: str) -> str:
        """Validate query text is not empty."""
        if not v or not v.strip():
            raise ValueError("Query text cannot be empty")
        return v.strip()

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, v: list[str]) -> list[str]:
        """Validate at least one domain is specified."""
        if not v or len(v) == 0:
            raise ValueError("At least one domain must be specified")
        return [d.strip() for d in v if d.strip()]

    @field_validator("depth_level")
    @classmethod
    def validate_depth_level(cls, v: str) -> str:
        """Validate depth level is valid."""
        valid_levels = ["survey", "comprehensive", "exhaustive"]
        if v not in valid_levels:
            raise ValueError(f"Invalid depth level. Must be one of: {valid_levels}")
        return v


class ResearchScope(BaseModel):
    """Model for research scope configuration."""

    depth_level: str = Field(default="comprehensive")
    domains: list[str] = Field(default_factory=list)
    time_period_start: datetime | None = None
    time_period_end: datetime | None = None
    languages: list[str] = Field(default_factory=lambda: ["en"])
    geographic_scope: list[str] = Field(default_factory=list)
    max_sources: int = Field(default=50, ge=1, le=1000)


class AgentTask(BaseModel):
    """Model for individual agent task."""

    id: UUID = Field(default_factory=uuid4)
    agent_type: str = Field(..., description="Type of agent to execute task")
    description: str = Field(..., description="Task description")
    priority: int = Field(..., ge=1, le=10)
    status: str = Field(default="pending")
    estimated_duration_seconds: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    result: dict | None = None

    def start(self) -> None:
        """Mark task as started."""
        self.status = "in_progress"
        self.started_at = datetime.utcnow()

    def complete(self, result: dict | None = None) -> None:
        """Mark task as completed."""
        self.status = "completed"
        self.completed_at = datetime.utcnow()
        if result:
            self.result = result

    def fail(self, error_message: str) -> None:
        """Mark task as failed."""
        self.status = "failed"
        self.error_message = error_message
        self.completed_at = datetime.utcnow()


class ResearchPlan(BaseModel):
    """Model for research execution plan."""

    id: UUID = Field(default_factory=uuid4)
    methodology: str = Field(..., description="Research methodology")
    tasks: list[AgentTask] = Field(default_factory=list)
    estimated_total_duration_seconds: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def add_task(self, task: AgentTask) -> None:
        """Add a task to the research plan."""
        self.tasks.append(task)
        if task.estimated_duration_seconds:
            if self.estimated_total_duration_seconds is None:
                self.estimated_total_duration_seconds = 0
            self.estimated_total_duration_seconds += task.estimated_duration_seconds


class ResearchProject(BaseModel):
    """Main research project model."""

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(..., min_length=1, max_length=500)
    query: ResearchQuery = Field(..., description="Research query")
    user_id: str = Field(..., description="User who created the project")
    status: ResearchStatus = Field(default=ResearchStatus.PENDING)
    scope: ResearchScope | None = None
    plan: ResearchPlan | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    results: dict | None = None
    metadata: dict = Field(default_factory=dict)

    def start_planning(self) -> None:
        """Transition to planning status."""
        if self.status != ResearchStatus.PENDING:
            raise ValueError(f"Cannot start planning from status: {self.status}")
        self.status = ResearchStatus.PLANNING
        self.updated_at = datetime.utcnow()

    def start_research(self) -> None:
        """Transition to in-progress status."""
        if self.status not in [ResearchStatus.PENDING, ResearchStatus.PLANNING]:
            raise ValueError(f"Cannot start research from status: {self.status}")
        self.status = ResearchStatus.IN_PROGRESS
        self.started_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def complete(self, results: dict | None = None) -> None:
        """Mark project as completed."""
        if self.status != ResearchStatus.IN_PROGRESS:
            raise ValueError(f"Cannot complete project from status: {self.status}")
        self.status = ResearchStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        if results:
            self.results = results

    def fail(self, error_message: str) -> None:
        """Mark project as failed."""
        self.status = ResearchStatus.FAILED
        self.error_message = error_message
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def cancel(self) -> None:
        """Cancel the project."""
        if self.status in [ResearchStatus.COMPLETED, ResearchStatus.FAILED]:
            raise ValueError(f"Cannot cancel project with status: {self.status}")
        self.status = ResearchStatus.CANCELLED
        self.updated_at = datetime.utcnow()

    def set_plan(self, plan: ResearchPlan) -> None:
        """Set the research plan for the project."""
        self.plan = plan
        self.updated_at = datetime.utcnow()

    def set_scope(self, scope: ResearchScope) -> None:
        """Set the research scope for the project."""
        self.scope = scope
        self.updated_at = datetime.utcnow()

    @property
    def is_active(self) -> bool:
        """Check if project is currently active."""
        return self.status in [
            ResearchStatus.PLANNING,
            ResearchStatus.IN_PROGRESS,
        ]

    @property
    def is_complete(self) -> bool:
        """Check if project is complete."""
        return self.status in [
            ResearchStatus.COMPLETED,
            ResearchStatus.FAILED,
            ResearchStatus.CANCELLED,
        ]

    @property
    def duration_seconds(self) -> int | None:
        """Calculate project duration in seconds."""
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds())
        return None


class ResearchProgress(BaseModel):
    """Model for tracking research progress."""

    project_id: UUID
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    in_progress_tasks: int = 0
    pending_tasks: int = 0
    progress_percentage: float = 0.0
    estimated_time_remaining_seconds: int | None = None
    current_agent: str | None = None
    current_agent_activities: list[dict] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    def update_from_tasks(self, tasks: list[AgentTask]) -> None:
        """Update progress from task list."""
        self.total_tasks = len(tasks)
        self.completed_tasks = sum(1 for t in tasks if t.status == "completed")
        self.failed_tasks = sum(1 for t in tasks if t.status == "failed")
        self.in_progress_tasks = sum(1 for t in tasks if t.status == "in_progress")
        self.pending_tasks = sum(1 for t in tasks if t.status == "pending")

        if self.total_tasks > 0:
            self.progress_percentage = (self.completed_tasks / self.total_tasks) * 100

        self.last_updated = datetime.utcnow()
