"""
Database migration for memory system.

Revision ID: 2026_03_01_memory_system
Revises: 
Create Date: 2026-03-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = '2026_03_01_memory_system'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Episodic memory events
    op.create_table(
        'episodic_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('event_data', postgresql.JSONB),
        sa.Column('query_text', sa.Text),
        sa.Column('quality_score', sa.Float),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )
    
    # Semantic entities
    op.create_table(
        'semantic_entities',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_type', sa.String(100), nullable=False),
        sa.Column('name', sa.String(500), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('embedding', postgresql.ARRAY(sa.Float)),
        sa.Column('mention_count', sa.Integer, default=1),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )
    
    # Entity relationships
    op.create_table(
        'entity_relationships',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('source_entity_id', postgresql.UUID(as_uuid=True)),
        sa.Column('target_entity_id', postgresql.UUID(as_uuid=True)),
        sa.Column('relationship_type', sa.String(100)),
        sa.Column('confidence', sa.Float)
    )
    
    # Facts
    op.create_table(
        'semantic_facts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True)),
        sa.Column('fact_type', sa.String(100)),
        sa.Column('fact_key', sa.String(255)),
        sa.Column('fact_value', sa.Text),
        sa.Column('confidence', sa.Float, default=0.5),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
    )
    
    # Research lineage
    op.create_table(
        'research_lineage',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('ancestor_project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('descendant_project_id', postgresql.UUID(as_uuid=True)),
        sa.Column('relationship_type', sa.String(50)),
        sa.Column('strength', sa.Float)
    )
    
    # Create indexes
    op.create_index('idx_episodic_events_user', 'episodic_events', ['user_id', 'created_at'])
    op.create_index('idx_semantic_entities_type', 'semantic_entities', ['entity_type'])


def downgrade():
    op.drop_table('research_lineage')
    op.drop_table('semantic_facts')
    op.drop_table('entity_relationships')
    op.drop_table('semantic_entities')
    op.drop_table('episodic_events')