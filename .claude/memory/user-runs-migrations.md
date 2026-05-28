---
name: user-runs-migrations
description: User runs DB migrations / schema-mutating commands himself — I change code and give him the commands
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bfcac8ac-7e73-4384-a543-7715ee5479ce
---

On agent-fastapi, when a change requires DB migrations (or other schema/DB-mutating commands), the user wants to **run them himself**. He said: "dont migrate just tell me the commands and their purpose. u just only change the code."

**Why:** he wants control over DB mutations (consistent with choosing `--access-mode=restricted` for the Postgres MCP).

**How to apply:** make the code changes (models, checkpoint store, etc.), then provide the exact commands with a one-line purpose each (e.g. `alembic downgrade base` — drop current schema; `alembic revision --autogenerate -m "..."` — generate migration from models; `alembic upgrade head` — apply). Do NOT execute alembic upgrade/downgrade or other DB-mutating commands myself. Read-only inspection (psql SELECT, pg_dump --schema-only) is fine. See [[prod-rigor-edge-cases]].
