"""Validate plans and dispatch the currently ready tasks."""
from graphlib import CycleError, TopologicalSorter

from .contracts import Conflict
from .plan_rules import validate_plan  # noqa: F401  v2 entry point; v1 keeps validate() below


def validate(plan):
    tasks = plan["tasks"]
    ids = {t["id"] for t in tasks}
    if not tasks or len(ids) != len(tasks):
        raise ValueError("任务为空或编号重复")
    if type(plan["max_parallel"]) is not int or plan["max_parallel"] < 1:
        raise ValueError("并发上限必须为正整数")
    for t in tasks:
        if not set(t["depends_on"]) <= ids:
            raise ValueError("存在未知前置任务")
        if t["source"] != plan["spec"]:
            raise ValueError("任务引用了不同版本的规格")
    try:
        tuple(TopologicalSorter({t["id"]: t["depends_on"] for t in tasks}).static_order())
    except CycleError as exc:
        raise ValueError("依赖成环：" + " → ".join(exc.args[1])) from exc


class Run:
    def __init__(self, plan):
        validate(plan)
        self.plan = plan
        self.tasks = {t["id"]: {"status": "waiting", "attempts": [], "artifacts": []}
                      for t in plan["tasks"]}
        self.events = []
        self.paused = False
        self.emit("run.started", plan_version=plan["version"])

    def emit(self, kind, **data):
        self.events.append({"seq": len(self.events) + 1, "kind": kind, **data})

    def ready(self):
        return [t for t in self.plan["tasks"]
                if self.tasks[t["id"]]["status"] == "waiting"
                and all(self.tasks[d]["status"] == "done" for d in t["depends_on"])]

    def step(self, execute):
        if self.paused:
            raise Conflict("派发已暂停")
        batch = self.ready()[:self.plan["max_parallel"]]
        if not batch:
            raise Conflict("没有就绪任务，请检查失败或待审核项")
        for task in batch:
            id_ = task["id"]
            row = self.tasks[id_]
            attempt_id = f'{id_}.{len(row["attempts"]) + 1}'
            inputs = [self.tasks[d]["artifacts"][-1] for d in task["depends_on"]]
            self.emit("task.dispatched", task_id=id_, attempt_id=attempt_id)
            try:
                result = execute({"attempt_id": attempt_id, "task": task, "inputs": inputs})
                if result["attempt_id"] != attempt_id or (result["ok"] and not result["content"]):
                    raise ValueError("回报编号或产物无效")
            except Exception:
                result = {"attempt_id": attempt_id, "ok": False, "error": "执行器未返回有效结果",
                          "content": None, "provider": "unknown", "usage": None}
            row["attempts"].append(result)
            if result["ok"]:
                row["artifacts"].append({"task_id": id_, "version": len(row["artifacts"]) + 1,
                                         "origin": "agent", "content": result["content"],
                                         "approved": not task["review_required"], "note": ""})
                row["status"] = "review" if task["review_required"] else "done"
            else:
                row["status"] = "failed"
            self.emit("task." + row["status"], task_id=id_, attempt_id=attempt_id)

    def review(self, task_id, version, decision, content, note):
        row = self.tasks.get(task_id)
        if not row or row["status"] != "review" or row["artifacts"][-1]["version"] != version:
            raise Conflict("产物已变化或当前无需审核")
        if not isinstance(note, str) or decision not in {"approve", "revise", "reject"}:
            raise ValueError("审核决定无效")
        draft = row["artifacts"][-1]
        if decision == "revise" and (not isinstance(content, str) or not content.strip()):
            raise ValueError("修订内容不能为空")
        record = {**draft, "version": version + 1, "origin": "human", "note": note,
                  "approved": decision != "reject",
                  "content": content.strip() if decision == "revise" else draft["content"]}
        row["artifacts"].append(record)
        row["status"] = "failed" if decision == "reject" else "done"
        self.emit("review." + decision, task_id=task_id, version=record["version"])

    def retry(self, task_id):
        if task_id not in self.tasks or self.tasks[task_id]["status"] != "failed":
            raise Conflict("仅可重试失败或退回任务")
        self.tasks[task_id]["status"] = "waiting"
        self.emit("task.retry_requested", task_id=task_id)

    def set_paused(self, paused):
        self.paused = paused
        self.emit("run.paused" if paused else "run.resumed")

    def snapshot(self):
        ready = {t["id"] for t in self.ready()}
        return {"paused": self.paused, "plan_version": self.plan["version"],
                "ready": sorted(ready), "tasks": self.tasks, "events": self.events,
                "complete": all(t["status"] == "done" for t in self.tasks.values())}
