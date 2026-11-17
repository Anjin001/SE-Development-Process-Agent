"""Plan scheduling (docs/接口契约-v2.md §3).

``schedule_plan(plan)`` returns ``[{task_id, early_start_seconds,
early_finish_seconds, critical}]`` or ``None`` when any estimate is missing.

Two things are computed separately, as the contract asks:

* ``early_start_seconds`` / ``early_finish_seconds`` come from a simulation
  that respects ``max_parallel`` and serialises tasks whose ``write_paths``
  overlap, so they include planned waiting;
* ``critical`` still means "zero total float on the pure dependency graph",
  so it never depends on concurrency or path contention.

Ties are broken by task id so the same plan always produces the same rows.
"""
from .plan_rules import overlaps


def _topological(tasks):
    deps = {task["id"]: list(task["depends_on"]) for task in tasks}
    order, seen = [], set()
    while len(order) < len(deps):
        batch = sorted(task_id for task_id in deps
                       if task_id not in seen and all(dep in seen for dep in deps[task_id]))
        if not batch:
            raise ValueError("任务依赖成环")
        for task_id in batch:
            order.append(task_id)
            seen.add(task_id)
    return order


def _simulate(plan):
    """Earliest start/finish including slots and write contention."""
    tasks = {task["id"]: task for task in plan["tasks"]}
    order = _topological(plan["tasks"])
    slots = plan["max_parallel"]
    start, finish = {}, {}
    pending = list(order)
    running = []          # [(finish_seconds, task_id)]
    now = 0
    while pending:
        progressed = True
        while progressed:
            progressed = False
            for task_id in list(pending):
                task = tasks[task_id]
                if not all(dep in finish for dep in task["depends_on"]):
                    continue
                ready_at = max([finish[dep] for dep in task["depends_on"]], default=0)
                if ready_at > now or len(running) >= slots:
                    continue
                paths = task["write_paths"]
                busy = [other for _, other in running]
                if any(overlaps(left, right)
                       for other in busy
                       for left in paths
                       for right in tasks[other]["write_paths"]):
                    continue
                start[task_id] = now
                finish[task_id] = now + task["estimate_seconds"]
                running.append((finish[task_id], task_id))
                pending.remove(task_id)
                progressed = True
        if pending:
            now = min(moment for moment, _ in running)
            running = [(moment, task_id) for moment, task_id in running if moment > now]
    return start, finish


def _critical(tasks, order):
    """Zero total float on the dependency-only graph."""
    durations = {task["id"]: task["estimate_seconds"] for task in tasks}
    deps = {task["id"]: task["depends_on"] for task in tasks}
    successors = {task["id"]: [] for task in tasks}
    for task in tasks:
        for dep in task["depends_on"]:
            successors[dep].append(task["id"])

    earliest = {}
    for task_id in order:
        earliest[task_id] = max([earliest[dep] + durations[dep]
                                 for dep in deps[task_id]], default=0)
    deadline = max(earliest[task_id] + durations[task_id] for task_id in order)

    latest = {}
    for task_id in reversed(order):
        latest[task_id] = min([latest[next_id] - durations[task_id]
                               for next_id in successors[task_id]],
                              default=deadline - durations[task_id])
    return {task_id: earliest[task_id] == latest[task_id] for task_id in order}


def schedule_plan(plan):
    tasks = plan["tasks"]
    if any(task.get("estimate_seconds") is None for task in tasks):
        return None
    order = _topological(tasks)
    start, finish = _simulate(plan)
    critical = _critical(tasks, order)
    return [{"task_id": task["id"],
             "early_start_seconds": start[task["id"]],
             "early_finish_seconds": finish[task["id"]],
             "critical": critical[task["id"]]} for task in tasks]