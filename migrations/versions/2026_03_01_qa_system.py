"""
Database migration for QA system.

Revision ID: 2026_03_01_qa_system
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '2026_03_01_qa_system'
down_revision = None
branch_labels = None


def upgrade():
    # Fact checks
    op.create_table(
        'fact_checks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('statement', sa.Text),
        sa.Column('verified', sa.Boolean),
        sa.Column('confidence', sa.Float),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )
    
    # Citation verifications
    op.create_table(
        'citation_verifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('citation', sa.Text),
        sa.Column('found', sa.Boolean),
        sa.Column('accessible', sa.Boolean),
        sa.Column('impact_score', sa.Float)
    )
    
    # Plagiarism checks
    op.create_table(
        'plagiarism_checks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('originality_score', sa.Float),
        sa.Column('matches', postgresql.JSONB)
    )
    
    # Peer reviews
    op.create_table(
        'peer_reviews',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('reviewer_id', postgresql.UUID(as_uuid=True)),
        sa.Column('status', sa.String(20)),
        sa.Column('overall_quality', sa.Integer),
        sa.Column('recommendation', sa.String(20)),
        sa.Column('submitted_at', sa.DateTime(timezone=True))
    )


def downgrade():
    op.drop_table('peer_reviews')
    op.drop_table('plagiarism_checks')
    op.drop_table('citation_verifications')
    op.drop_table('fact_checks')