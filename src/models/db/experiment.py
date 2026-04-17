"""
Experiment database models for A/B Testing System.

This module provides the database schema for tracking experiments,
variants, assignments, and results across the Cerebro AI Brain platform.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class ExperimentStatus(enum.Enum):
    """Experiment lifecycle status."""
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ExperimentType(enum.Enum):
    """Type of experiment being run."""
    PROMPT = "prompt"
    ROUTING = "routing"
    API_PATTERN = "api_pattern"
    MEMORY = "memory"
    SUPERVISOR = "supervisor"
    MODEL = "model"
    SYSTEM = "system"


class AllocationStrategy(enum.Enum):
    """Traffic allocation strategy for experiments."""
    RANDOM = "random"
    WEIGHTED = "weighted"
    DETERMINISTIC = "deterministic"
    ADAPTIVE = "adaptive"
    CONTEXTUAL = "contextual"


class Experiment(BaseModel):
    """Main experiment model for A/B testing."""

    __tablename__ = "experiments"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    experiment_type: Mapped[ExperimentType] = mapped_column(Enum(ExperimentType), nullable=False)
    status: Mapped[ExperimentStatus] = mapped_column(Enum(ExperimentStatus), default=ExperimentStatus.DRAFT)

    allocation_strategy: Mapped[AllocationStrategy] = mapped_column(Enum(AllocationStrategy), default=AllocationStrategy.RANDOM)
    traffic_percentage: Mapped[float] = mapped_column(Float, default=100.0)  # % of traffic to include
    target_segments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # User segments to target

    # Scheduling
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Configuration
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # Experiment-specific configuration
    metrics: Mapped[list[str]] = mapped_column(JSON, default=list)  # Metrics to track
    success_criteria: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # Success criteria
    
    # Statistical Settings
    min_sample_size: Mapped[int] = mapped_column(Integer, default=1000)
    confidence_level: Mapped[float] = mapped_column(Float, default=0.95)
    power: Mapped[float] = mapped_column(Float, default=0.8)
    expected_effect_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Relationships
    variants = relationship("ExperimentVariant", back_populates="experiment", cascade="all, delete-orphan")
    assignments = relationship("ExperimentAssignment", back_populates="experiment", cascade="all, delete-orphan")
    results = relationship("ExperimentResult", back_populates="experiment", cascade="all, delete-orphan")
    
    # Indexes
    __table_args__ = (
        Index("idx_experiment_status", "status"),
        Index("idx_experiment_type", "experiment_type"),
        Index("idx_experiment_dates", "start_date", "end_date"),
    )


class ExperimentVariant(BaseModel):
    """Variant (treatment or control) within an experiment."""
    
    __tablename__ = "experiment_variants"
    
    # Foreign Keys
    experiment_id = Column(PGUUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False)
    
    # Variant Information
    name = Column(String(100), nullable=False)  # e.g., "control", "treatment_a"
    description = Column(Text)
    is_control = Column(Boolean, default=False)
    
    # Allocation
    allocation_percentage = Column(Float, nullable=False)  # % of experiment traffic
    
    # Configuration
    config = Column(JSON, default=dict)  # Variant-specific configuration
    
    # Parameters (for different experiment types)
    parameters = Column(JSON, default=dict)
    # For PROMPT: {"prompt_template": "...", "temperature": 0.7}
    # For ROUTING: {"strategy": "cost_efficient", "threshold": 0.8}
    # For API_PATTERN: {"pattern": "primary", "fallback": true}
    
    # Relationships
    experiment = relationship("Experiment", back_populates="variants")
    assignments = relationship("ExperimentAssignment", back_populates="variant")
    results = relationship("ExperimentResult", back_populates="variant")
    
    # Constraints
    __table_args__ = (
        UniqueConstraint("experiment_id", "name", name="uq_experiment_variant_name"),
        Index("idx_variant_experiment", "experiment_id"),
    )


class ExperimentAssignment(BaseModel):
    """User/session assignment to experiment variants."""
    
    __tablename__ = "experiment_assignments"
    
    # Foreign Keys
    experiment_id = Column(PGUUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False)
    variant_id = Column(PGUUID(as_uuid=True), ForeignKey("experiment_variants.id"), nullable=False)
    
    # Assignment Information
    user_id = Column(String(255))  # Can be user ID, session ID, or request ID
    assignment_key = Column(String(255), nullable=False)  # Unique key for assignment
    
    # Context
    context = Column(JSON, default=dict)  # Context at assignment time
    # e.g., {"query_complexity": "high", "domain": "research", "model": "gemini-pro"}
    
    # Timestamps
    assigned_at = Column(DateTime, default=datetime.utcnow)
    exposed_at = Column(DateTime)  # When user was actually exposed to variant
    
    # Relationships
    experiment = relationship("Experiment", back_populates="assignments")
    variant = relationship("ExperimentVariant", back_populates="assignments")
    
    # Constraints and Indexes
    __table_args__ = (
        UniqueConstraint("experiment_id", "assignment_key", name="uq_experiment_assignment"),
        Index("idx_assignment_experiment_variant", "experiment_id", "variant_id"),
        Index("idx_assignment_user", "user_id"),
        Index("idx_assignment_timestamp", "assigned_at"),
    )


class ExperimentResult(BaseModel):
    """Results and metrics from experiment execution."""
    
    __tablename__ = "experiment_results"
    
    # Foreign Keys
    experiment_id = Column(PGUUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False)
    variant_id = Column(PGUUID(as_uuid=True), ForeignKey("experiment_variants.id"), nullable=False)
    assignment_id = Column(PGUUID(as_uuid=True), ForeignKey("experiment_assignments.id"))
    
    # Metric Information
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Float, nullable=False)
    
    # Additional Data
    metadata_: Any = Column("metadata", JSON, default=dict)
    # e.g., {"latency_ms": 150, "cost_usd": 0.002, "quality_score": 0.95}
    
    # Context
    context = Column(JSON, default=dict)
    # e.g., {"query": "...", "agent_used": "research", "model": "gemini-pro"}
    
    # Timestamps
    recorded_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    experiment = relationship("Experiment", back_populates="results")
    variant = relationship("ExperimentVariant", back_populates="results")
    
    # Indexes
    __table_args__ = (
        Index("idx_result_experiment_variant", "experiment_id", "variant_id"),
        Index("idx_result_metric", "metric_name"),
        Index("idx_result_timestamp", "recorded_at"),
    )


class ExperimentAnalysis(BaseModel):
    """Stored analysis results for experiments."""
    
    __tablename__ = "experiment_analyses"
    
    # Foreign Key
    experiment_id = Column(PGUUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False)
    
    # Analysis Type
    analysis_type = Column(String(50), nullable=False)  # "interim", "final", "bayesian"
    
    # Statistical Results
    results = Column(JSON, nullable=False)
    # {
    #   "control_mean": 0.5,
    #   "treatment_mean": 0.55,
    #   "p_value": 0.03,
    #   "confidence_interval": [0.01, 0.09],
    #   "effect_size": 0.1,
    #   "statistical_power": 0.85,
    #   "sample_sizes": {"control": 5000, "treatment": 5000}
    # }
    
    # Recommendations
    recommendation = Column(Text)
    confidence_score = Column(Float)  # 0-1 confidence in recommendation
    
    # Metadata
    analyzed_at = Column(DateTime, default=datetime.utcnow)
    analyst = Column(String(100))  # System or user who ran analysis
    
    # Indexes
    __table_args__ = (
        Index("idx_analysis_experiment", "experiment_id"),
        Index("idx_analysis_type", "analysis_type"),
        Index("idx_analysis_timestamp", "analyzed_at"),
    )