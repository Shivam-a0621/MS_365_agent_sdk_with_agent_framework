---
name: agent-fastapi-architecture
description: "agent-fastapi persistence + memory architecture (two-family schema with aistudiobot_* / aistudiobot_agent_* prefixes, user-message anchoring via seq, SDK Storage, HITL, checkpoint pruning)"
metadata: 
  node_type: memory
  type: project
  originSessionId: bfcac8ac-7e73-4384-a543-7715ee5479ce
---

Key decisions for the agent-fastapi Teams handoff-agent service (DB = `agent-fastapi`, async SQLAlchemy + asyncpg + Alembic; user runs migrations himself — see [[user-runs-migrations]]).

**Schema (`db/models.py`) — TWO DECOUPLED FAMILIES in one schema, chatbot39-style.** chatbot39 split `aistudiobot_*` platform tables from `custom_*` app tables, with NO FK crossing the boundary; we follow the same pattern (and the SAME `aistudiobot_*` platform prefix):
- **Platform / SDK family** (`aistudiobot_` prefix): `aistudiobot_bot`, `aistudiobot_channel`, `aistudiobot_botchannelmapping`, `aistudiobot_chatsession`, `aistudiobot_chatconversation`, `aistudiobot_chathistory`, `aistudiobot_store` (SDK Storage KV). Hard FKs WITHIN this family.
- **Agent-framework family** (`aistudiobot_agent_` prefix): **`aistudiobot_agent_actions`** (the agent action log — handoffs, tool calls, approvals; framework-bound event types + payload shapes), `aistudiobot_agent_llm_call`, `aistudiobot_agent_human_input`, `aistudiobot_agent_checkpoint`. Swap the framework → only these change.
- **One-way dependency rule:** an `aistudiobot_agent_*` row carries platform keys (`chat_conversation_id`, `user_message_id`) as plain INDEXED columns with **NO foreign key** (soft refs). No FK crosses the boundary in either direction.

**No `agent_turn` table.** The user `aistudiobot_chathistory` row IS the per-query anchor:
- A user row is written for EVERY inbound Activity (text OR card submit; the `activity` JSONB column carries the value payload even when `text=""`).
- `aistudiobot_agent_actions` / `aistudiobot_agent_llm_call` / `aistudiobot_agent_human_input` rows point at it via `user_message_id` (soft ref, no FK).
- **No `parent_id` on chathistory** — assistant rows sit between user rows in the shared `seq` order; lineage is implicit (`WHERE chat_conversation_id=X ORDER BY seq`).
- Turn-level "status" is derived, not persisted: awaiting-approval ⇔ `aistudiobot_agent_human_input.status='open'` for the conversation; completed ⇔ no open approval.

**`aistudiobot_chatsession` columns:** `agent_conversation_id String(150)` (the channel-level conversation ref string, e.g. the Teams `bot_framework_conversation_id` value — column renamed off the legacy Bot Framework era name) and `agent_channel_mapping_id` (FK into `aistudiobot_botchannelmapping`).

**Activity column.** `aistudiobot_chathistory.activity JSONB` holds the real channel Activity dict — `bot.py:_activity_to_dict()` serializes `context.activity` (pydantic v2 `model_dump(mode="json")` with v1/`to_dict` fallbacks).

**Ordering — shared Postgres SEQUENCE.** `aistudiobot_chathistory.seq` and `aistudiobot_agent_actions.seq` both default to `nextval('aistudiobot_event_seq')`. Replay = `SELECT … UNION ALL … WHERE chat_conversation_id=X ORDER BY seq`. No MAX+1 race, no per-write SELECT. Numbers are gappy and global; doesn't matter for ordering.

**SDK Storage is Postgres-backed** (`db/storage.py` `PostgresStorage(Storage)` → `aistudiobot_store(key, data)`, mirrors chatbot39 `aistudiobot_store`). Replaced `MemoryStorage()` at `bot.py:34`. Mirrors `MemoryStorage` exactly (`store_item_to_json`/`from_json_to_store_item`, NO etag — the per-conversation Redis lock serializes a conversation's turns). **In our current handlers we never read/write `state.conversation`/`state.user`, so `aistudiobot_store` stays empty** — durable plumbing for when we add OAuth user-token persistence (the SDK's authorization layer uses Storage). Redis is a later sibling-class drop-in.

**Conversation boundary:** `aistudiobot_chatconversation` is 1:1 with `aistudiobot_chatsession` (one per Teams `agent_conversation_id`).

**Memory model:**
- Postgres **checkpoints** (`PostgresCheckpointStorage` → `aistudiobot_agent_checkpoint`) for durable HITL pause/resume; isolated per conversation via `workflow_name` = `it_support__{conversation_id}__v{version}`.
- **Pruning to {floor, latest}** via `aistudiobot_agent_checkpoint.is_floor`. `manager.finalize_checkpoints(latest_checkpoint_id, completed)` → `storage.mark_floor` (only on a completed turn) + `storage.prune` (delete non-floor non-latest). Each checkpoint is a full self-contained snapshot and `load` reads one row, so pruning others is safe.
- **History stays in Postgres** `aistudiobot_chathistory` (not Redis); `RedisHistoryProvider(max_messages=N)` is a planned later upgrade.
- **Redis for per-conversation distributed lock** (`services/locks.py`, TTL 180s / blocking 120s, fail-open on outage). Run multi-worker via `uvicorn app:app --workers N`. DB pool per worker (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`, default 5/10).

**HITL is durable + stateless across processes** (spike-proven): resume reconstructs the approval `Content` from stored primitives; deny is graceful. See [[agent-framework-hitl-facts]].

**Agent prompt rules.** All handoff agents share `HANDOFF_AGENT_RULES` in `agents/handoff_agents.py` + a one-line `_role(...)` anchor each. Core principles: (1) always entitled to do your own work — just call tools, including approval-required ones; (2) never reply for another agent; (3) never narrate handoffs or future plans (don't say "I'll hand off…", "Next I will…", etc.); (4) speak only about finished work (real tool result). Also: treat each user request independently of past actions in agent history (so prior TKT-1001 doesn't satisfy a fresh "raise a ticket"). `scripts/reset_session.py [<conversation_ref>|--all]` clears framework state (checkpoint + open human_input) between tests so stale per-agent history can't poison the LLM.

Hold the [[prod-rigor-edge-cases]] bar.
