"""
API endpoints for A/B Testing experiment management.

This module provides REST API endpoints for creating, managing, and
analyzing experiments across the Cerebro AI Brain platform.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.database import get_db
from src.models.db.experiment import (
    Experiment, ExperimentVariant, ExperimentAssignment,
    ExperimentResult, ExperimentStatus, ExperimentType,
    AllocationStrategy
)
from src.auth.dependencies import get_current_user
from src.models.user import User


# Pydantic models for API
class VariantCreate(BaseModel):
    """Model for creating an experiment variant."""
    name: str = Field(..., description="Variant name (e.g., control, treatment_a)")
    description: Optional[str] = Field(None, description="Variant description")
    is_control: bool = Field(False, description="Is this the control variant?")
    allocation_percentage: float = Field(..., description="Percentage of traffic for this variant")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Variant parameters")


class ExperimentCreate(BaseModel):
    """Model for creating a new experiment."""
    name: str = Field(..., description="Unique experiment name")
    description: Optional[str] = Field(None, description="Experiment description")
    experiment_type: ExperimentType = Field(..., description="Type of experiment")
    allocation_strategy: AllocationStrategy = Field(
        AllocationStrategy.RANDOM,
        description="Traffic allocation strategy"
    )
    traffic_percentage: float = Field(100.0, description="Percentage of total traffic to include")
    variants: List[VariantCreate] = Field(..., description="Experiment variants")
    metrics: List[str] = Field(default_factory=list, description="Metrics to track")
    success_criteria: Dict[str, Any] = Field(
        default_factory=dict,
        description="Success criteria for the experiment"
    )
    min_sample_size: int = Field(1000, description="Minimum sample size per variant")
    confidence_level: float = Field(0.95, description="Statistical confidence level")
    start_date: Optional[datetime] = Field(None, description="Experiment start date")
    end_date: Optional[datetime] = Field(None, description="Experiment end date")


class ExperimentUpdate(BaseModel):
    """Model for updating an experiment."""
    description: Optional[str] = None
    status: Optional[ExperimentStatus] = None
    traffic_percentage: Optional[float] = None
    end_date: Optional[datetime] = None
    success_criteria: Optional[Dict[str, Any]] = None


class ExperimentResponse(BaseModel):
    """Response model for experiment data."""
    id: UUID
    name: str
    description: Optional[str]
    experiment_type: ExperimentType
    status: ExperimentStatus
    allocation_strategy: AllocationStrategy
    traffic_percentage: float
    variants: List[Dict[str, Any]]
    metrics: List[str]
    success_criteria: Dict[str, Any]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class AssignmentRequest(BaseModel):
    """Request model for getting variant assignment."""
    user_id: str = Field(..., description="User or session ID")
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Context for assignment (e.g., query complexity, domain)"
    )


class AssignmentResponse(BaseModel):
    """Response model for variant assignment."""
    experiment_id: UUID
    variant_id: UUID
    variant_name: str
    parameters: Dict[str, Any]
    assignment_key: str


class MetricRecord(BaseModel):
    """Model for recording experiment metrics."""
    assignment_id: UUID = Field(..., description="Assignment ID from variant assignment")
    metric_name: str = Field(..., description="Name of the metric")
    metric_value: float = Field(..., description="Metric value")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class ExperimentAnalysisResponse(BaseModel):
    """Response model for experiment analysis."""
    experiment_id: UUID
    analysis_type: str
    results: Dict[str, Any]
    recommendation: Optional[str]
    confidence_score: Optional[float]
    analyzed_at: datetime


# Create router
router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


@router.post("/", response_model=ExperimentResponse)
async def create_experiment(
    experiment: ExperimentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ExperimentResponse:
    """
    Create a new A/B testing experiment.
    
    This endpoint creates an experiment with variants and configuration
    for running A/B tests across the Cerebro platform.
    """
    # Validate variant allocations sum to 100%
    total_allocation = sum(v.allocation_percentage for v in experiment.variants)
    if abs(total_allocation - 100.0) > 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Variant allocations must sum to 100%, got {total_allocation}%"
        )
    
    # Create experiment
    db_experiment = Experiment(
        name=experiment.name,
        description=experiment.description,
        experiment_type=experiment.experiment_type,
        status=ExperimentStatus.DRAFT,
        allocation_strategy=experiment.allocation_strategy,
        traffic_percentage=experiment.traffic_percentage,
        metrics=experiment.metrics,
        success_criteria=experiment.success_criteria,
        min_sample_size=experiment.min_sample_size,
        confidence_level=experiment.confidence_level,
        start_date=experiment.start_date,
        end_date=experiment.end_date,
        created_by=str(current_user.id)
    )
    
    # Add variants
    for variant_data in experiment.variants:
        variant = ExperimentVariant(
            name=variant_data.name,
            description=variant_data.description,
            is_control=variant_data.is_control,
            allocation_percentage=variant_data.allocation_percentage,
            parameters=variant_data.parameters
        )
        db_experiment.variants.append(variant)
    
    db.add(db_experiment)
    await db.commit()
    await db.refresh(db_experiment)
    
    return ExperimentResponse(
        id=db_experiment.id,
        name=db_experiment.name,
        description=db_experiment.description,
        experiment_type=db_experiment.experiment_type,
        status=db_experiment.status,
        allocation_strategy=db_experiment.allocation_strategy,
        traffic_percentage=db_experiment.traffic_percentage,
        variants=[{
            "id": v.id,
            "name": v.name,
            "is_control": v.is_control,
            "allocation_percentage": v.allocation_percentage,
            "parameters": v.parameters
        } for v in db_experiment.variants],
        metrics=db_experiment.metrics,
        success_criteria=db_experiment.success_criteria,
        start_date=db_experiment.start_date,
        end_date=db_experiment.end_date,
        created_at=db_experiment.created_at,
        updated_at=db_experiment.updated_at
    )


@router.get("/", response_model=List[ExperimentResponse])
async def list_experiments(
    status: Optional[ExperimentStatus] = Query(None, description="Filter by status"),
    experiment_type: Optional[ExperimentType] = Query(None, description="Filter by type"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> List[ExperimentResponse]:
    """
    List all experiments with optional filtering.
    
    Returns a list of experiments, optionally filtered by status or type.
    """
    from sqlalchemy import select
    
    query = select(Experiment)
    
    if status:
        query = query.where(Experiment.status == status)
    if experiment_type:
        query = query.where(Experiment.experiment_type == experiment_type)
    
    result = await db.execute(query)
    experiments = result.scalars().all()
    
    return [
        ExperimentResponse(
            id=exp.id,
            name=exp.name,
            description=exp.description,
            experiment_type=exp.experiment_type,
            status=exp.status,
            allocation_strategy=exp.allocation_strategy,
            traffic_percentage=exp.traffic_percentage,
            variants=[{
                "id": v.id,
                "name": v.name,
                "is_control": v.is_control,
                "allocation_percentage": v.allocation_percentage,
                "parameters": v.parameters
            } for v in exp.variants],
            metrics=exp.metrics,
            success_criteria=exp.success_criteria,
            start_date=exp.start_date,
            end_date=exp.end_date,
            created_at=exp.created_at,
            updated_at=exp.updated_at
        )
        for exp in experiments
    ]


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(
    experiment_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ExperimentResponse:
    """
    Get a specific experiment by ID.
    
    Returns detailed information about a single experiment.
    """
    from sqlalchemy import select
    
    result = await db.execute(
        select(Experiment).where(Experiment.id == experiment_id)
    )
    experiment = result.scalar_one_or_none()
    
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    
    return ExperimentResponse(
        id=experiment.id,
        name=experiment.name,
        description=experiment.description,
        experiment_type=experiment.experiment_type,
        status=experiment.status,
        allocation_strategy=experiment.allocation_strategy,
        traffic_percentage=experiment.traffic_percentage,
        variants=[{
            "id": v.id,
            "name": v.name,
            "is_control": v.is_control,
            "allocation_percentage": v.allocation_percentage,
            "parameters": v.parameters
        } for v in experiment.variants],
        metrics=experiment.metrics,
        success_criteria=experiment.success_criteria,
        start_date=experiment.start_date,
        end_date=experiment.end_date,
        created_at=experiment.created_at,
        updated_at=experiment.updated_at
    )


@router.patch("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
    experiment_id: UUID,
    update: ExperimentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ExperimentResponse:
    """
    Update an experiment's configuration.
    
    Allows updating experiment status, traffic percentage, and other settings.
    """
    from sqlalchemy import select
    
    result = await db.execute(
        select(Experiment).where(Experiment.id == experiment_id)
    )
    experiment = result.scalar_one_or_none()
    
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    
    # Update fields
    if update.description is not None:
        experiment.description = update.description
    if update.status is not None:
        experiment.status = update.status
    if update.traffic_percentage is not None:
        experiment.traffic_percentage = update.traffic_percentage
    if update.end_date is not None:
        experiment.end_date = update.end_date
    if update.success_criteria is not None:
        experiment.success_criteria = update.success_criteria
    
    experiment.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(experiment)
    
    return ExperimentResponse(
        id=experiment.id,
        name=experiment.name,
        description=experiment.description,
        experiment_type=experiment.experiment_type,
        status=experiment.status,
        allocation_strategy=experiment.allocation_strategy,
        traffic_percentage=experiment.traffic_percentage,
        variants=[{
            "id": v.id,
            "name": v.name,
            "is_control": v.is_control,
            "allocation_percentage": v.allocation_percentage,
            "parameters": v.parameters
        } for v in experiment.variants],
        metrics=experiment.metrics,
        success_criteria=experiment.success_criteria,
        start_date=experiment.start_date,
        end_date=experiment.end_date,
        created_at=experiment.created_at,
        updated_at=experiment.updated_at
    )


@router.post("/{experiment_id}/assign", response_model=AssignmentResponse)
async def assign_variant(
    experiment_id: UUID,
    request: AssignmentRequest,
    db: AsyncSession = Depends(get_db)
) -> AssignmentResponse:
    """
    Get variant assignment for a user/session.
    
    This endpoint determines which variant a user should be assigned to
    based on the experiment's allocation strategy.
    """
    from sqlalchemy import select
    import hashlib
    import random
    
    # Get experiment
    result = await db.execute(
        select(Experiment).where(Experiment.id == experiment_id)
    )
    experiment = result.scalar_one_or_none()
    
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    
    if experiment.status != ExperimentStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Experiment is not running (status: {experiment.status})"
        )
    
    # Check if user is in experiment traffic percentage
    assignment_key = f"{experiment_id}:{request.user_id}"
    
    # Check for existing assignment
    existing = await db.execute(
        select(ExperimentAssignment).where(
            ExperimentAssignment.experiment_id == experiment_id,
            ExperimentAssignment.assignment_key == assignment_key
        )
    )
    existing_assignment = existing.scalar_one_or_none()
    
    if existing_assignment:
        # Return existing assignment
        variant = await db.get(ExperimentVariant, existing_assignment.variant_id)
        return AssignmentResponse(
            experiment_id=experiment_id,
            variant_id=variant.id,
            variant_name=variant.name,
            parameters=variant.parameters,
            assignment_key=assignment_key
        )
    
    # Determine if user should be in experiment
    if experiment.traffic_percentage < 100.0:
        user_hash = int(hashlib.md5(request.user_id.encode()).hexdigest(), 16)
        if (user_hash % 100) >= experiment.traffic_percentage:
            # User not in experiment traffic
            control_variant = next(
                (v for v in experiment.variants if v.is_control),
                experiment.variants[0]
            )
            return AssignmentResponse(
                experiment_id=experiment_id,
                variant_id=control_variant.id,
                variant_name=control_variant.name,
                parameters=control_variant.parameters,
                assignment_key=assignment_key
            )
    
    # Assign variant based on allocation strategy
    if experiment.allocation_strategy == AllocationStrategy.RANDOM:
        # Random weighted assignment
        rand = random.random() * 100
        cumulative = 0.0
        selected_variant = None
        
        for variant in experiment.variants:
            cumulative += variant.allocation_percentage
            if rand <= cumulative:
                selected_variant = variant
                break
        
        if not selected_variant:
            selected_variant = experiment.variants[-1]
    
    elif experiment.allocation_strategy == AllocationStrategy.DETERMINISTIC:
        # Hash-based deterministic assignment
        user_hash = int(hashlib.md5(request.user_id.encode()).hexdigest(), 16)
        bucket = user_hash % 100
        cumulative = 0.0
        selected_variant = None
        
        for variant in experiment.variants:
            cumulative += variant.allocation_percentage
            if bucket < cumulative:
                selected_variant = variant
                break
        
        if not selected_variant:
            selected_variant = experiment.variants[-1]
    
    else:
        # For other strategies, default to first variant for now
        selected_variant = experiment.variants[0]
    
    # Create assignment
    assignment = ExperimentAssignment(
        experiment_id=experiment_id,
        variant_id=selected_variant.id,
        user_id=request.user_id,
        assignment_key=assignment_key,
        context=request.context
    )
    db.add(assignment)
    await db.commit()
    
    return AssignmentResponse(
        experiment_id=experiment_id,
        variant_id=selected_variant.id,
        variant_name=selected_variant.name,
        parameters=selected_variant.parameters,
        assignment_key=assignment_key
    )


@router.post("/{experiment_id}/metrics")
async def record_metric(
    experiment_id: UUID,
    metric: MetricRecord,
    db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    """
    Record a metric for an experiment.
    
    This endpoint records performance metrics for experiment analysis.
    """
    # Verify assignment exists
    from sqlalchemy import select
    
    result = await db.execute(
        select(ExperimentAssignment).where(
            ExperimentAssignment.id == metric.assignment_id,
            ExperimentAssignment.experiment_id == experiment_id
        )
    )
    assignment = result.scalar_one_or_none()
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # Record metric
    result = ExperimentResult(
        experiment_id=experiment_id,
        variant_id=assignment.variant_id,
        assignment_id=assignment.id,
        metric_name=metric.metric_name,
        metric_value=metric.metric_value,
        metadata=metric.metadata
    )
    
    db.add(result)
    await db.commit()
    
    return JSONResponse(
        status_code=200,
        content={"message": "Metric recorded successfully"}
    )


@router.get("/{experiment_id}/analysis", response_model=ExperimentAnalysisResponse)
async def get_experiment_analysis(
    experiment_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ExperimentAnalysisResponse:
    """
    Get analysis results for an experiment.
    
    Returns statistical analysis comparing variants in the experiment.
    """
    from sqlalchemy import select, func
    from src.models.db.experiment import ExperimentAnalysis
    
    # Check if experiment exists
    experiment = await db.get(Experiment, experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    
    # Get latest analysis
    result = await db.execute(
        select(ExperimentAnalysis)
        .where(ExperimentAnalysis.experiment_id == experiment_id)
        .order_by(ExperimentAnalysis.analyzed_at.desc())
        .limit(1)
    )
    analysis = result.scalar_one_or_none()
    
    if not analysis:
        # Perform basic analysis
        # This is a simplified version - real implementation would use
        # the statistical engine from the A/B testing system
        
        # Get results grouped by variant
        results_query = select(
            ExperimentResult.variant_id,
            func.count(ExperimentResult.id).label("count"),
            func.avg(ExperimentResult.metric_value).label("mean"),
            func.stddev(ExperimentResult.metric_value).label("stddev")
        ).where(
            ExperimentResult.experiment_id == experiment_id
        ).group_by(ExperimentResult.variant_id)
        
        results = await db.execute(results_query)
        variant_stats = {row.variant_id: row for row in results}
        
        # Simple comparison
        control_variant = next(
            (v for v in experiment.variants if v.is_control),
            experiment.variants[0] if experiment.variants else None
        )
        
        if control_variant and control_variant.id in variant_stats:
            control_stats = variant_stats[control_variant.id]
            
            analysis_results = {
                "control_mean": float(control_stats.mean or 0),
                "sample_sizes": {},
                "variant_means": {}
            }
            
            for variant in experiment.variants:
                if variant.id in variant_stats:
                    stats = variant_stats[variant.id]
                    analysis_results["sample_sizes"][variant.name] = stats.count
                    analysis_results["variant_means"][variant.name] = float(stats.mean or 0)
            
            # Create analysis record
            analysis = ExperimentAnalysis(
                experiment_id=experiment_id,
                analysis_type="interim",
                results=analysis_results,
                recommendation="Continue experiment to gather more data",
                confidence_score=0.5,
                analyst="system"
            )
            db.add(analysis)
            await db.commit()
            await db.refresh(analysis)
    
    return ExperimentAnalysisResponse(
        experiment_id=analysis.experiment_id,
        analysis_type=analysis.analysis_type,
        results=analysis.results,
        recommendation=analysis.recommendation,
        confidence_score=analysis.confidence_score,
        analyzed_at=analysis.analyzed_at
    )