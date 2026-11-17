"""Offline worker adapter. Replaced later by a worktree-backed agent."""

def execute(request):
    task = request["task"]
    refs = ", ".join(f'{a["task_id"]}@v{a["version"]}' for a in request["inputs"]) or "无"
    return {"attempt_id": request["attempt_id"], "ok": True,
            "content": f'{task["title"]}\n规格 {task["source"]}\n上游版本 {refs}\n交付 {task["output"]}',
            "error": None, "provider": "mock", "usage": None}
