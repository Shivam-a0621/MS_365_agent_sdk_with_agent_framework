---
name: prod-rigor-edge-cases
description: "agent-fastapi is a real production project — analyze every edge case, no demo shortcuts"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bfcac8ac-7e73-4384-a543-7715ee5479ce
---

On the agent-fastapi Teams handoff-agent project, the user insists this is a **real production project**, not a POC/demo. When I offered a "Tier 1 in-memory shortcut vs Tier 2 durable" framing for human-in-the-loop approvals, they pushed back: "we are working on real prod project so take everything seriously and analyze every edge case and minor details."

**Why:** They want correctness and durability, not the easiest path. They notice and dislike speculative over-engineering too (e.g. questioned an unused `position` column), so the bar is *production-correct AND no dead weight* — every element justified.

**How to apply:** For every design, explicitly enumerate edge cases — concurrency (double-submit, same-conversation races, multi-worker), restart/durability, partial/lost state, failure modes (MCP down, DB errors), idempotency, stale/expired requests, ordering/atomicity, security. Default to the durable/correct solution (Postgres-backed checkpoints, distributed locks, transactions) and justify it; don't present in-memory shortcuts as the recommended path. Ground designs by reading the actual `agent_framework` internals rather than assuming.
