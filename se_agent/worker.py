"""Worker adapter: hand one WorkerRequest to an executor and record the result.

The orchestration side owns attempts, leases and release; the executor only
answers one request at a time. ``run_once`` is the loop the runner plugs into:
swap ``simulated`` for 胡子昂's real executor and nothing above changes.
"""
from .contracts import InvalidState, InvalidReference


def simulated(request):
    """Stand-in executor: one artifact per declared output, no real work."""
    artifacts = []
    for output in request["task"]["outputs"]:
        if output["kind"] == "git_commit":
            artifacts.append({"output_id": output["id"], "kind": "git_commit",
                              "commit_sha": "0" * 40,
                              "changed_paths": request["task"]["write_paths"],
                              "content": None})
        else:
            artifacts.append({"output_id": output["id"], "kind": "text",
                              "commit_sha": None, "changed_paths": [],
                              "content": f'{request["task"]["title"]} 的交付说明'})
    return {"attempt_id": request["attempt_id"], "status": "succeeded",
            "artifacts": artifacts, "error": None, "provider": "mock",
            "model": None, "usage": None, "checkout_commit": None,
            "started_at": None, "ended_at": None}


def _failure(request, message, code="EXECUTION_FAILED"):
    return {"attempt_id": request["attempt_id"], "status": "failed",
            "artifacts": [], "error": {"code": code, "message": message,
                                       "retryable": True, "details": {}},
            "provider": "unknown", "model": None, "usage": None,
            "checkout_commit": None, "started_at": None, "ended_at": None}


def run_once(store, execute, run_id, worker_id="worker-1", lease_seconds=300,
             limit=None):
    """Claim one batch, execute each request, submit each result."""
    if not run_id:
        raise InvalidReference("需要指定运行编号", field="run_id")
    handed = store.claim(run_id, worker_id, lease_seconds=lease_seconds, limit=limit)
    results = []
    for request in handed:
        try:
            result = execute(request)
        except Exception as exc:                      # a crash is a reportable result
            result = _failure(request, str(exc))
        if not isinstance(result, dict):
            result = _failure(request, "执行器未返回结果对象")
        result = dict(result)
        result["attempt_id"] = request["attempt_id"]
        store.submit(run_id, result, worker_id)
        results.append(result)
    return results


def drive(store, execute, run_id, worker_id="worker-1", lease_seconds=300,
          rounds=100):
    """Dispatch until nothing is left to hand out.

    Automatic runs claim whatever is ready. Manual runs first fix a batch with
    ``step``; an empty or exhausted queue simply ends this round.
    """
    processed = []
    for _ in range(rounds):
        store.expire_leases(run_id)
        snapshot = store.snapshot(run_id)
        if snapshot["state"] == "completed":
            break
        if snapshot["mode"] == "manual":
            try:
                store.step(run_id)
            except InvalidState:
                pass                     # queue still open, or nothing to dispatch
        batch = run_once(store, execute, run_id, worker_id, lease_seconds)
        if not batch:
            break
        processed.extend(batch)
    return processed