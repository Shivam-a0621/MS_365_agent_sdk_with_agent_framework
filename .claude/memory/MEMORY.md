# Memory Index

- [Prod rigor / edge cases](prod-rigor-edge-cases.md) — agent-fastapi is a real prod project; analyze every edge case, no demo shortcuts, no dead weight.
- [agent_framework HITL facts](agent-framework-hitl-facts.md) — verified checkpoint + approval/resume behavior & gotchas (denial crashes, checkpoint keyed by workflow_name, response reconstructable, request_id stable).
- [User runs migrations](user-runs-migrations.md) — change code + give him the alembic commands; he runs DB-mutating commands himself.
- [agent-fastapi architecture](agent-fastapi-architecture.md) — schema (agent_* mirror of chatbot39), 1:1 session↔conversation, hybrid memory (checkpoints + RedisHistoryProvider max_messages).
- [Git identity](git-identity.md) — commit as Shivam-a0621 / bhardwajshivam.2108@gmail.com (set locally); push straight to main.
