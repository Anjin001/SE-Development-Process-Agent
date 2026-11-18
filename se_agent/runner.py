"""Worker 适配入口。

v1 的 execute() 继续供模拟回归使用；v2 的正式入口是 execute_task()，
接 WorkerRequest、回 WorkerResult，见 docs/接口契约-v2.md §5。
"""
from .worker import execute_task

__all__ = ["execute", "execute_task"]


def execute(request):
    """Offline worker adapter. Replaced later by a worktree-backed agent."""
    task = request["task"]
    refs = ", ".join(f'{a["task_id"]}@v{a["version"]}' for a in request["inputs"]) or "无"
    return {"attempt_id": request["attempt_id"], "ok": True,
            "content": f'{task["title"]}\n规格 {task["source"]}\n上游版本 {refs}\n交付 {task["output"]}',
            "error": None, "provider": "mock", "usage": None}
