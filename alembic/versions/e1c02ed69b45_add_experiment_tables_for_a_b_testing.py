"""Add experiment tables for A/B testing

Revision ID: e1c02ed69b45
Revises: 5bf6a31d1957
Create Date: 2025-09-08 09:25:08.270031

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1c02ed69b45'
down_revision: str | Sequence[str] | None = '5bf6a31d1957'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create enums
    op.execute("CREATE TYPE experimentstatus AS ENUM ('draft', 'running', 'paused', 'completed', 'archived')")
    op.execute("CREATE TYPE experimenttype AS ENUM ('prompt', 'routing', 'api_pattern', 'memory', 'supervisor', 'model', 'system')")
    op.execute("CREATE TYPE allocationstrategy AS ENUM ('random', 'weighted', 'deterministic', 'adaptive', 'contextual')")
    
    # Create experiments table
    op.create_table(
        'experiments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('experiment_type', sa.Enum('prompt', 'routing', 'api_pattern', 'memory', 'supervisor', 'model', 'system', name='experimenttype'), nullable=False),
        sa.Column('status', sa.Enum('draft', 'running', 'paused', 'completed', 'archived', name='experimentstatus'), nullable=True),
        sa.Column('allocation_strategy', sa.Enum('random', 'weighted', 'deterministic', 'adaptive', 'contextual', name='allocationstrategy'), nullable=True),
        sa.Column('traffic_percentage', sa.Float(), nullable=True),
        sa.Column('target_segments', sa.JSON(), nullable=True),
        sa.Column('start_date', sa.DateTime(), nullable=True),
        sa.Column('end_date', sa.DateTime(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('success_criteria', sa.JSON(), nullable=True),
        sa.Column('min_sample_size', sa.Integer(), nullable=True),
        sa.Column('confidence_level', sa.Float(), nullable=True),
        sa.Column('power', sa.Float(), nullable=True),
        sa.Column('expected_effect_size', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('updated_by', sa.String(255), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index('idx_experiment_status', 'experiments', ['status'], unique=False)
    op.create_index('idx_experiment_type', 'experiments', ['experiment_type'], unique=False)
    op.create_index('idx_experiment_dates', 'experiments', ['start_date', 'end_date'], unique=False)
    
    # Create experiment_variants table
    op.create_table(
        'experiment_variants',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('experiment_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_control', sa.Boolean(), nullable=True),
        sa.Column('allocation_percentage', sa.Float(), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('updated_by', sa.String(255), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('experiment_id', 'name', name='uq_experiment_variant_name')
    )
    op.create_index('idx_variant_experiment', 'experiment_variants', ['experiment_id'], unique=False)
    
    # Create experiment_assignments table
    op.create_table(
        'experiment_assignments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('experiment_id', sa.UUID(), nullable=False),
        sa.Column('variant_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=True),
        sa.Column('assignment_key', sa.String(255), nullable=False),
        sa.Column('context', sa.JSON(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(), nullable=True),
        sa.Column('exposed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('updated_by', sa.String(255), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
        sa.ForeignKeyConstraint(['variant_id'], ['experiment_variants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('experiment_id', 'assignment_key', name='uq_experiment_assignment')
    )
    op.create_index('idx_assignment_experiment_variant', 'experiment_assignments', ['experiment_id', 'variant_id'], unique=False)
    op.create_index('idx_assignment_user', 'experiment_assignments', ['user_id'], unique=False)
    op.create_index('idx_assignment_timestamp', 'experiment_assignments', ['assigned_at'], unique=False)
    
    # Create experiment_results table
    op.create_table(
        'experiment_results',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('experiment_id', sa.UUID(), nullable=False),
        sa.Column('variant_id', sa.UUID(), nullable=False),
        sa.Column('assignment_id', sa.UUID(), nullable=True),
        sa.Column('metric_name', sa.String(100), nullable=False),
        sa.Column('metric_value', sa.Float(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('context', sa.JSON(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('updated_by', sa.String(255), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['assignment_id'], ['experiment_assignments.id'], ),
        sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
        sa.ForeignKeyConstraint(['variant_id'], ['experiment_variants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_result_experiment_variant', 'experiment_results', ['experiment_id', 'variant_id'], unique=False)
    op.create_index('idx_result_metric', 'experiment_results', ['metric_name'], unique=False)
    op.create_index('idx_result_timestamp', 'experiment_results', ['recorded_at'], unique=False)
    
    # Create experiment_analyses table
    op.create_table(
        'experiment_analyses',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('experiment_id', sa.UUID(), nullable=False),
        sa.Column('analysis_type', sa.String(50), nullable=False),
        sa.Column('results', sa.JSON(), nullable=False),
        sa.Column('recommendation', sa.Text(), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('analyzed_at', sa.DateTime(), nullable=True),
        sa.Column('analyst', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('updated_by', sa.String(255), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_analysis_experiment', 'experiment_analyses', ['experiment_id'], unique=False)
    op.create_index('idx_analysis_type', 'experiment_analyses', ['analysis_type'], unique=False)
    op.create_index('idx_analysis_timestamp', 'experiment_analyses', ['analyzed_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tables in reverse order
    op.drop_index('idx_analysis_timestamp', table_name='experiment_analyses')
    op.drop_index('idx_analysis_type', table_name='experiment_analyses')
    op.drop_index('idx_analysis_experiment', table_name='experiment_analyses')
    op.drop_table('experiment_analyses')
    
    op.drop_index('idx_result_timestamp', table_name='experiment_results')
    op.drop_index('idx_result_metric', table_name='experiment_results')
    op.drop_index('idx_result_experiment_variant', table_name='experiment_results')
    op.drop_table('experiment_results')
    
    op.drop_index('idx_assignment_timestamp', table_name='experiment_assignments')
    op.drop_index('idx_assignment_user', table_name='experiment_assignments')
    op.drop_index('idx_assignment_experiment_variant', table_name='experiment_assignments')
    op.drop_table('experiment_assignments')
    
    op.drop_index('idx_variant_experiment', table_name='experiment_variants')
    op.drop_table('experiment_variants')
    
    op.drop_index('idx_experiment_dates', table_name='experiments')
    op.drop_index('idx_experiment_type', table_name='experiments')
    op.drop_index('idx_experiment_status', table_name='experiments')
    op.drop_table('experiments')
    
    # Drop enums
    op.execute("DROP TYPE allocationstrategy")
    op.execute("DROP TYPE experimenttype")
    op.execute("DROP TYPE experimentstatus")
