---
name: agent-framework-hitl-facts
description: Verified agent_framework checkpoint + human-in-the-loop behavior (gotchas) — read before designing HITL/persistence
metadata: 
  node_type: memory
  type: reference
  originSessionId: bfcac8ac-7e73-4384-a543-7715ee5479ce
---

Code-grounded facts about `agent_framework` (v1.5, venv at /home/shivam/bot2agent/.venv) HITL + workflow checkpointing, verified by reading source. Use when designing approval/resume/persistence in agent-fastapi. See [[prod-rigor-edge-cases]].

- **Denial = hard failure (landmine):** resuming a function approval with `approved=False` makes the agent raise `AgentInvalidResponseException` (`agent_framework/_agents.py` ~line 789) → workflow emits a "failed" event. There is NO "tool skipped, agent continues" path. Deny must be handled in the app layer (abandon the paused run, inform user, reset state) — never resume with approved=False.
- **Approval response is reconstructable from primitives** (no live event needed → durable/stateless resume): `Content.from_function_approval_response(approved=bool, id=request_id, function_call=Content.from_function_call(call_id, name, arguments))` (`_types.py` ~1228-1296). Persist {request_id, call_id, function_name, arguments} to rebuild.
- **Checkpoints keyed by `workflow_name` only** (`CheckpointStorage.get_latest/list_checkpoints(workflow_name=...)`). Many conversations sharing one name COLLIDE. Isolate per conversation: use a unique workflow name per conversation (e.g. `it_support__{conversation_id}__v{ver}`) AND store the latest checkpoint_id on the conversation row, resume by explicit id.
- **Checkpoint auto-saved at every superstep end** (incl. when paused on request_info); `WorkflowCheckpoint.pending_request_info_events` is populated. `checkpoint_id` is a UUID, NOT returned by `run()` — obtain via `storage.get_latest(workflow_name=...)` or track yourself.
- **`run()` param rules** (`_workflow.py` ~845): `message` XOR `responses`; `message` XOR `checkpoint_id`; `checkpoint_id`+`responses` allowed (restore-then-respond); at least one required. Responses are coerced/validated against the request's `response_type` (`try_coerce_to_type` + `is_instance_of`).
- **Partial responses OK:** answering a subset of pending request_ids resolves those; the rest stay pending and re-emit next run.
- **`request_id` stable across checkpoint save→restore** (persisted in checkpoint). Safe as the card `approval_id` correlation key.
- **Concurrency guard:** `Workflow.run()` sets `_is_running`; a concurrent run on the same instance raises `RuntimeError("Workflow is already running...")`. Serialize per conversation with a lock.
- **`graph_signature_hash`** covers executor CLASS types + edge topology, NOT instructions/tools. Changing a prompt/tool does NOT invalidate old checkpoints (resumes with new prompt); changing the agent set/ids/classes DOES → resume raises `WorkflowCheckpointException` (`_runner.py` ~275). Bump a workflow_version in the name to intentionally invalidate.
