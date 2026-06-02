"""Stage-1 spike for the long-running-task feature (no DB, no Teams).

Proves the lightweight task workflow:
  1. pauses on ctx.request_info (a durable checkpoint is written), and
  2. resumes from that checkpoint in a FRESH workflow instance with a plain JSON dict that the
     framework coerces into the declared TaskResult dataclass — yielding TaskCompleted.

Run: python -m scripts.spike_task
"""

from __future__ import annotations

import asyncio

from agent_framework import InMemoryCheckpointStorage

from workflows.task_flow import TaskCompleted, TaskStart, build_task_workflow, task_workflow_name


async def main() -> None:
    storage = InMemoryCheckpointStorage()
    conv, cid = 1, "corr-abc"

    # 1. SPAWN — run the task workflow to its pause.
    wf = build_task_workflow(conversation_id=conv, correlation_id=cid, checkpoint_storage=storage)
    result = await wf.run(
        TaskStart(correlation_id=cid, task_type="demo", summary="a long job"),
        include_status_events=True,
    )
    reqs = list(result.get_request_info_events())
    assert reqs, "expected a pending request_info (the pause)"
    request_id = reqs[0].request_id

    name = task_workflow_name(conv, cid)
    checkpoints = await storage.list_checkpoints(workflow_name=name)
    assert checkpoints, "expected a checkpoint saved at the pause"
    checkpoint_id = checkpoints[-1].checkpoint_id
    print(f"PAUSED  request_id={request_id}  checkpoint_id={checkpoint_id}  checkpoints={len(checkpoints)}")

    # 2. CALLBACK RESUME — rehydrate a fresh instance and resume with a JSON dict (coerced to TaskResult).
    wf2 = build_task_workflow(conversation_id=conv, correlation_id=cid, checkpoint_storage=storage)
    result2 = await wf2.run(
        responses={request_id: {"correlation_id": cid, "status": "ok", "output": {"rows": 42}}},
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
        include_status_events=True,
    )
    completed = [o for o in result2.get_outputs() if isinstance(o, TaskCompleted)]
    assert completed, f"expected a TaskCompleted output, got: {result2.get_outputs()}"
    done = completed[0]
    print(
        f"RESUMED corr={done.correlation_id} status={done.result.status} "
        f"output={done.result.output} result_type={type(done.result).__name__}"
    )
    assert done.result.status == "ok" and done.result.output == {"rows": 42}, "result not coerced correctly"
    print("STAGE 1 OK — task workflow pauses, checkpoints, and resumes with a coerced TaskResult")


if __name__ == "__main__":
    asyncio.run(main())
