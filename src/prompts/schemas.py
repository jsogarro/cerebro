"""
Prompt Template Schemas

Defines Pydantic schemas for prompt templates, enabling validation,
type safety, and structured prompt management.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PromptType(StrEnum):
    """Types of prompts in the system."""

    AGENT = "agent"
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    REFINEMENT = "refinement"
    SYSTEM = "system"
    TASK = "task"
    DOMAIN = "domain"


class PromptRole(StrEnum):
    """Agent roles for prompts."""

    RESEARCH_SUPERVISOR = "research_supervisor"
    CONTENT_SUPERVISOR = "content_supervisor"
    ANALYTICS_SUPERVISOR = "analytics_supervisor"

    # Research workers
    LITERATURE_ANALYST = "literature_analyst"
    METHODOLOGY_SPECIALIST = "methodology_specialist"
    DATA_SYNTHESIZER = "data_synthesizer"
    CITATION_VALIDATOR = "citation_validator"

    # Content workers
    CONTENT_STRATEGIST = "content_strategist"
    TECHNICAL_WRITER = "technical_writer"
    CONTENT_EDITOR = "content_editor"
    SEO_OPTIMIZER = "seo_optimizer"

    # Analytics workers
    DATA_COLLECTOR = "data_collector"
    STATISTICAL_ANALYST = "statistical_analyst"
    VISUALIZATION_SPECIALIST = "visualization_specialist"
    BUSINESS_ANALYST = "business_analyst"

    # Special roles
    EVALUATION_SUPERVISOR = "evaluation_supervisor"
    CONSENSUS_FACILITATOR = "consensus_facilitator"


class VariableType(StrEnum):
    """Variable types for prompt templates."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    LIST = "list"
    DICT = "dict"
    OPTIONAL = "optional"


class PromptVariable(BaseModel):
    """Variable definition for prompt templates."""

    name: str = Field(..., description="Variable name")
    type: VariableType = Field(..., description="Variable type")
    description: str = Field("", description="Variable description")
    required: bool = Field(True, description="Whether variable is required")
    default: Any | None = Field(None, description="Default value if not provided")
    validation_rules: list[str] = Field(
        default_factory=list, description="Validation rules"
    )
    examples: list[Any] = Field(default_factory=list, description="Example values")


class PromptExample(BaseModel):
    """Example input/output for few-shot learning."""

    input_variables: dict[str, Any] = Field(..., description="Example input variables")
    expected_output: str = Field(..., description="Expected output for this input")
    explanation: str = Field("", description="Why this is a good example")
    tags: list[str] = Field(default_factory=list, description="Example tags")


class PromptMetadata(BaseModel):
    """Metadata for prompt templates."""

    name: str = Field(..., description="Prompt name")
    version: str = Field("1.0.0", description="Prompt version")
    description: str = Field("", description="Prompt description")

    # Categorization
    type: PromptType = Field(..., description="Prompt type")
    role: PromptRole | None = Field(None, description="Target agent role")
    domain: str | None = Field(None, description="Target domain")

    # Authorship and maintenance
    author: str = Field("Cerebro Team", description="Prompt author")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Usage and performance
    usage_count: int = Field(0, description="Number of times used")
    success_rate: float = Field(0.0, description="Success rate (0-1)")
    avg_quality_score: float = Field(0.0, description="Average quality score")

    # Optimization
    tags: list[str] = Field(default_factory=list, description="Prompt tags")
    complexity_score: float = Field(0.5, description="Prompt complexity (0-1)")
    estimated_tokens: int = Field(500, description="Estimated token usage")

    # A/B Testing
    ab_test_group: str | None = Field(None, description="A/B test group")
    champion_version: bool = Field(True, description="Is this the champion version")


class PromptTemplate(BaseModel):
    """Complete prompt template structure."""

    # Template metadata
    metadata: PromptMetadata = Field(..., description="Prompt metadata")

    # Template content
    system_prompt: str = Field("", description="System prompt template")
    user_prompt: str = Field("", description="User prompt template")
    assistant_prompt: str = Field("", description="Assistant prompt template")

    # Template configuration
    variables: list[PromptVariable] = Field(
        default_factory=list, description="Template variables"
    )
    examples: list[PromptExample] = Field(
        default_factory=list, description="Few-shot examples"
    )

    # Output specification
    expected_output_schema: dict[str, Any] | None = Field(
        None, description="Expected JSON output schema"
    )
    output_format: str = Field(
        "json", description="Output format (json, text, structured)"
    )

    # Template behavior
    max_tokens: int = Field(4000, description="Maximum tokens for response")
    temperature: float = Field(0.7, description="Default temperature")

    # Advanced features
    inherits_from: str | None = Field(
        None, description="Base template to inherit from"
    )
    requires_refinement: bool = Field(
        False, description="Requires multi-round refinement"
    )
    consensus_threshold: float = Field(0.95, description="Required consensus level")

    # Safety and validation
    safety_checks: list[str] = Field(
        default_factory=list, description="Safety validation rules"
    )
    quality_checks: list[str] = Field(
        default_factory=list, description="Quality validation rules"
    )

    @field_validator("metadata")
    @classmethod
    def validate_metadata_consistency(cls, v: PromptMetadata) -> PromptMetadata:
        """Ensure metadata is consistent with template content."""
        # Could add validation logic here
        return v

    @field_validator("temperature")
    @classmethod
    def validate_temperature_range(cls, v: float) -> float:
        """Validate temperature is in valid range."""
        if not 0.0 <= v <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        return v


class PromptCollection(BaseModel):
    """Collection of related prompt templates."""

    name: str = Field(..., description="Collection name")
    description: str = Field("", description="Collection description")
    version: str = Field("1.0.0", description="Collection version")

    templates: dict[str, PromptTemplate] = Field(
        default_factory=dict, description="Template name to template mapping"
    )

    # Collection metadata
    domain: str | None = Field(None, description="Target domain")
    agent_types: list[str] = Field(
        default_factory=list, description="Compatible agent types"
    )
    dependencies: list[str] = Field(
        default_factory=list, description="Required dependencies"
    )

    # Performance tracking
    total_usage: int = Field(0, description="Total usage across all templates")
    avg_success_rate: float = Field(0.0, description="Average success rate")

    def get_template(self, template_name: str) -> PromptTemplate | None:
        """Get a specific template from the collection."""
        return self.templates.get(template_name)

    def get_templates_by_role(self, role: PromptRole) -> dict[str, PromptTemplate]:
        """Get all templates for a specific role."""
        return {
            name: template
            for name, template in self.templates.items()
            if template.metadata.role == role
        }

    def get_templates_by_type(
        self, prompt_type: PromptType
    ) -> dict[str, PromptTemplate]:
        """Get all templates of a specific type."""
        return {
            name: template
            for name, template in self.templates.items()
            if template.metadata.type == prompt_type
        }


__all__ = [
    "PromptCollection",
    "PromptExample",
    "PromptMetadata",
    "PromptRole",
    "PromptTemplate",
    "PromptType",
    "PromptVariable",
    "VariableType",
]
