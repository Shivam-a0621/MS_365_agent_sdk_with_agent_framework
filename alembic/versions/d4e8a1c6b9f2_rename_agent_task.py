"""rename aistudio_agent_task -> aistudiobot_agent_task (match the aistudiobot_ prefix)

Every other table uses the aistudiobot_ prefix; this aligns the background-task table. Forward-only
rename (table + its indexes + pk constraint) so it applies cleanly on an existing DB.

Revision ID: d4e8a1c6b9f2
Revises: c2e5a8b1f9d0
Create Date: 2026-06-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd4e8a1c6b9f2'
down_revision: Union[str, None] = 'c2e5a8b1f9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('aistudio_agent_task', 'aistudiobot_agent_task')
    op.execute("ALTER INDEX ix_aistudio_agent_task_correlation_id RENAME TO ix_aistudiobot_agent_task_correlation_id")
    op.execute("ALTER INDEX ix_aistudio_agent_task_chat_conversation_id RENAME TO ix_aistudiobot_agent_task_chat_conversation_id")
    op.execute("ALTER TABLE aistudiobot_agent_task RENAME CONSTRAINT pk_aistudio_agent_task TO pk_aistudiobot_agent_task")


def downgrade() -> None:
    op.execute("ALTER TABLE aistudiobot_agent_task RENAME CONSTRAINT pk_aistudiobot_agent_task TO pk_aistudio_agent_task")
    op.execute("ALTER INDEX ix_aistudiobot_agent_task_chat_conversation_id RENAME TO ix_aistudio_agent_task_chat_conversation_id")
    op.execute("ALTER INDEX ix_aistudiobot_agent_task_correlation_id RENAME TO ix_aistudio_agent_task_correlation_id")
    op.rename_table('aistudiobot_agent_task', 'aistudio_agent_task')
