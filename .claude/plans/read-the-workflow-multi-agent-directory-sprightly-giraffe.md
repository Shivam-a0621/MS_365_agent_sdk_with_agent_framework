# Refactor 3: aistudio→aistudiobot prefix, chatsession column renames, drop parent_id

## Context

Three small naming + structural alignments:

1. **Prefix `aistudio_*` → `aistudiobot_*`** everywhere (table names + the shared sequence). This matches chatbot39's `aistudiobot_*` precedent exactly — same word, including the platform-flavored "bot".
2. **In `aistudiobot_chatsession`, rename two legacy columns**:
   - `bot_framework_conversation_id` → `agent_conversation_id`
   - `bot_channel_mapping_id` → `agent_channel_mapping_id`
   The `bot_framework_` prefix is from the old Bot Framework era; we're on the MS 365 Agents SDK now, so "agent" fits.
3. **Drop `parent_id` from `aistudiobot_chathistory`**. Transcript becomes a flat sequence ordered by `seq`. Assistant rows no longer self-ref the user row; the relationship is implicit through `seq` order and through the `user_message_id` soft-ref still present on `aistudiobot_agent_actions` / `aistudiobot_agent_human_input` / `aistudiobot_agent_llm_call`.

This is a fresh-DB rebuild (same workflow as the previous prefix change).

## Changes

### `db/models.py`
- Every `__tablename__` changes `aistudio_*` → `aistudiobot_*`:
  - `aistudiobot_bot`, `aistudiobot_channel`, `aistudiobot_botchannelmapping`, `aistudiobot_chatsession`, `aistudiobot_chatconversation`, `aistudiobot_chathistory`, `aistudiobot_store`, `aistudiobot_agent_actions`, `aistudiobot_agent_llm_call`, `aistudiobot_agent_human_input`, `aistudiobot_agent_checkpoint`.
- All `ForeignKey("aistudio_*.id")` strings update to `aistudiobot_*.id`.
- Sequence rename: `event_seq = Sequence("aistudio_event_seq", metadata=Base.metadata)` → `Sequence("aistudiobot_event_seq", metadata=Base.metadata)`.
- Update index names (`ix_aistudio_*`, `uq_aistudio_*`, `pk_aistudio_*`) → `aistudiobot_*` counterparts. Alembic will pick most of these up via the naming convention in `db/base.py`; the explicit `Index(...)` names in `__table_args__` need a manual swap (`ix_aistudio_agent_checkpoint_name_ts`, `uq_aistudio_agent_human_input_conv_request`, `ix_aistudio_agent_human_input_open`).
- **`ChatSession` column renames** (the type/length stay):
  - `bot_framework_conversation_id: Mapped[str] = mapped_column(String(150))` → `agent_conversation_id: Mapped[str] = mapped_column(String(150))`
  - `bot_channel_mapping_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("aistudio_botchannelmapping.id"))` → `agent_channel_mapping_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("aistudiobot_botchannelmapping.id"))`
- **`ChatHistory`**: drop the `parent_id` column (and its index).
- Module docstring family lineup updated to the new prefix; per-class docstrings updated where they reference table names.

### `db/repositories.py`
- `get_or_create_session(...)`: replace usage of `bot_framework_conversation_id` with `agent_conversation_id`; replace the inserted `bot_channel_mapping_id=…` with `agent_channel_mapping_id=…`. The function's keyword arg name (`conversation_ref` / `bot_channel_mapping_id`) gets aligned: rename `bot_channel_mapping_id` parameter → `agent_channel_mapping_id` to match the new column.
- `add_chat_message(...)`: drop the `parent_id` parameter and stop passing it to the row constructor.

### `services/conversation_service.py`
- Caller of `repo.get_or_create_session`: pass `agent_channel_mapping_id=mapping.id` instead of `bot_channel_mapping_id=mapping.id`.
- User-inbound `add_chat_message(...)` call: drop `parent_id=None` (the kwarg disappears from the signature).
- `_record_approval_card_outbound(...)`: drop the `parent_id` argument from signature; drop `parent_id=user_message_id` from the inner `add_chat_message` call. The card row still lives in `aistudiobot_chathistory` ordered by `seq` — lineage to the user request is implicit (it's the most recent user row before the next user row).
- Docstrings on `_record_approval_card_outbound` updated: drop the "parent_id chain" framing; substitute "next row in seq" framing.

### `workflows/recorder.py`
- The `add_chat_message(...)` call for assistant text drops `parent_id=user_message_id`. The `user_message_id` parameter to `record_run` STAYS — still needed for `repo.add_action(user_message_id=…)` (action soft-refs the user message).

### Docstrings / comments referencing old prefix
- `db/storage.py`, `db/checkpoint_store.py`, `agents/middleware.py`, `services/conversation_service.py` step-6 comment, `workflows/recorder.py` event-to-row comment block — table-name mentions update to `aistudiobot_*`.

### Scripts
- `scripts/reset_session.py`: raw SQL — `aistudio_agent_checkpoint` → `aistudiobot_agent_checkpoint`, `aistudio_agent_human_input` → `aistudiobot_agent_human_input`; the conv-lookup query swaps `bot_framework_conversation_id` → `agent_conversation_id` (and the joined table to `aistudiobot_chatsession` / `aistudiobot_chatconversation`).
- `scripts/spike_service.py`: every raw SQL table name + the conv-lookup join condition.
- `scripts/spike_hitl.py`: every raw SQL `DELETE FROM aistudio_*` / `INSERT INTO aistudio_*` becomes `aistudiobot_*`; the `INSERT INTO aistudio_chatsession` column list swaps `bot_framework_conversation_id` → `agent_conversation_id` and `bot_channel_mapping_id` → `agent_channel_mapping_id`.

### Memory
- `~/.claude/projects/-home-shivam-bot2agent-agent-fastapi/memory/agent-fastapi-architecture.md` — prefix in the family listing + column-name mentions.

## Migration (user runs; fresh-DB rebuild)

```bash
cd /home/shivam/bot2agent/agent-fastapi && export VIRTUAL_ENV=/home/shivam/bot2agent/.venv

# 1. wipe DB
psql -h localhost -U postgres -c 'DROP DATABASE IF EXISTS "agent-fastapi";'
psql -h localhost -U postgres -c 'CREATE DATABASE "agent-fastapi";'

# 2. clear the prior initial migration so this is a single clean revision
rm alembic/versions/*.py

# 3. regenerate from the new models
uv run --active alembic revision --autogenerate -m "initial: aistudiobot_* prefix + chatsession column renames + no parent_id"

# 4. review the autogen — if CREATE SEQUENCE for aistudiobot_event_seq is missing,
#    add op.execute("CREATE SEQUENCE IF NOT EXISTS aistudiobot_event_seq") at the top
#    of upgrade() and the matching DROP SEQUENCE at the bottom of downgrade()
#    (same patch we applied the last two times).
uv run --active alembic upgrade head
```

## Verification

1. `python -c "import bot"` clean.
2. `psql -d agent-fastapi -c "\dt"` → 11 tables, all `aistudiobot_*`.
3. `psql -d agent-fastapi -c "\d aistudiobot_chatsession"` → columns `agent_conversation_id`, `agent_channel_mapping_id` present; **no** `bot_framework_conversation_id` / `bot_channel_mapping_id`.
4. `psql -d agent-fastapi -c "\d aistudiobot_chathistory"` → **no** `parent_id` column.
5. `psql -d agent-fastapi -c "\ds"` → sequence `aistudiobot_event_seq`.
6. `uv run python -m scripts.spike_service` (or live Teams) — full handoff with two approvals still works; transcript rows land in `aistudiobot_chathistory` ordered by `seq`; action rows in `aistudiobot_agent_actions` with `user_message_id` pointing at the user inbound.

## One inconsistency to flag

`agent_channel_mapping_id` (column on `chatsession`) points at table `aistudiobot_botchannelmapping` — column says "agent_channel" but the FK target table still contains "bot". I'm implementing the literal column rename; if you want fully consistent naming, two options:
- Skip the `bot_channel_mapping_id` column rename (keep it matching the table name).
- Also rename the table to `aistudiobot_agentchannelmapping` (or similar) — bigger churn.

Default in this plan is the literal rename you asked for; tell me if you'd prefer either alternative and I'll adjust before implementing.
