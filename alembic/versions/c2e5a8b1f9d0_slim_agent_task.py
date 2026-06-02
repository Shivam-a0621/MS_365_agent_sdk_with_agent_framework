"""slim aistudio_agent_task (drop the separate-task-workflow columns)

The long-running-task design was simplified: the task no longer gets its own workflow/checkpoint
lineage — the conversation's own between-turns checkpoint is the resume point. So the columns that
tracked a separate task workflow are no longer needed.

Revision ID: c2e5a8b1f9d0
Revises: b1f3c7a9d2e4
Create Date: 2026-06-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c2e5a8b1f9d0'
down_revision: Union[str, None] = 'b1f3c7a9d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('aistudio_agent_task', sa.Column('summary', sa.Text(), nullable=True))
    op.drop_index('ix_aistudio_agent_task_pending', table_name='aistudio_agent_task', postgresql_where=sa.text("status = 'pending'"))
    for col in ('task_workflow_name', 'request_id', 'checkpoint_id', 'task_type', 'params', 'expires_at'):
        op.drop_column('aistudio_agent_task', col)


def downgrade() -> None:
    op.add_column('aistudio_agent_task', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('aistudio_agent_task', sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('aistudio_agent_task', sa.Column('task_type', sa.String(length=64), nullable=True))
    op.add_column('aistudio_agent_task', sa.Column('checkpoint_id', sa.String(length=64), nullable=True))
    op.add_column('aistudio_agent_task', sa.Column('request_id', sa.String(length=128), nullable=True))
    op.add_column('aistudio_agent_task', sa.Column('task_workflow_name', sa.String(length=256), nullable=True))
    op.create_index('ix_aistudio_agent_task_pending', 'aistudio_agent_task', ['status'], unique=False, postgresql_where=sa.text("status = 'pending'"))
    op.drop_column('aistudio_agent_task', 'summary')
