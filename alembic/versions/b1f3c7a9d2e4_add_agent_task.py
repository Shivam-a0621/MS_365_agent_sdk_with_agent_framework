"""add aistudio_agent_task (long-running background tasks)

Revision ID: b1f3c7a9d2e4
Revises: 7b8618a72ca3
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b1f3c7a9d2e4'
down_revision: Union[str, None] = '7b8618a72ca3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'aistudio_agent_task',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('correlation_id', sa.String(length=128), nullable=False),
        sa.Column('chat_conversation_id', sa.BigInteger(), nullable=False),
        sa.Column('channel', sa.String(length=64), nullable=False),
        sa.Column('conversation_ref', sa.String(length=150), nullable=False),
        sa.Column('task_workflow_name', sa.String(length=256), nullable=False),
        sa.Column('request_id', sa.String(length=128), nullable=False),
        sa.Column('checkpoint_id', sa.String(length=64), nullable=True),
        sa.Column('task_type', sa.String(length=64), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_aistudio_agent_task')),
    )
    op.create_index(
        op.f('ix_aistudio_agent_task_correlation_id'),
        'aistudio_agent_task', ['correlation_id'], unique=True,
    )
    op.create_index(
        op.f('ix_aistudio_agent_task_chat_conversation_id'),
        'aistudio_agent_task', ['chat_conversation_id'], unique=False,
    )
    op.create_index(
        'ix_aistudio_agent_task_pending',
        'aistudio_agent_task', ['status'], unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('ix_aistudio_agent_task_pending', table_name='aistudio_agent_task', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_aistudio_agent_task_chat_conversation_id'), table_name='aistudio_agent_task')
    op.drop_index(op.f('ix_aistudio_agent_task_correlation_id'), table_name='aistudio_agent_task')
    op.drop_table('aistudio_agent_task')
