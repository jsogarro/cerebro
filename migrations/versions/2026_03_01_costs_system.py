"""
Database migration for costs system.

Revision ID: 2026_03_01_costs_system
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '2026_03_01_costs_system'
down_revision = None


def upgrade():
    # Cost transactions
    op.create_table(
        'cost_transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('transaction_id', sa.String(255), unique=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True)),
        sa.Column('project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('user_id', postgresql.UUID(as_uuid=True)),
        sa.Column('model_id', sa.String(100)),
        sa.Column('input_tokens', sa.Integer),
        sa.Column('output_tokens', sa.Integer),
        sa.Column('total_cost_cents', sa.Integer),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )
    
    # Budgets
    op.create_table(
        'budgets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('scope_type', sa.String(20)),
        sa.Column('scope_id', postgresql.UUID(as_uuid=True)),
        sa.Column('amount_cents', sa.Integer),
        sa.Column('period_type', sa.String(20)),
        sa.Column('alert_thresholds', postgresql.ARRAY(sa.Integer)),
        sa.Column('is_active', sa.Boolean, default=True)
    )
    
    # Indexes
    op.create_index('idx_cost_transactions_org', 'cost_transactions', 
                   ['organization_id', 'created_at'])


def downgrade():
    op.drop_table('budgets')
    op.drop_table('cost_transactions')