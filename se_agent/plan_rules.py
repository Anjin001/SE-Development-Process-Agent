"""v2 plan validation for the orchestration entry point.

``validate_plan(plan, confirmed_spec)`` implements docs/接口契约-v2.md §3:
task ids, source references, direct predecessors, self loops, cycles and path
legality, each raising a locatable error. It is deliberately separate from the
v1 ``orchestrator.validate`` so the mock regression keeps its own rules and its
existing error text ("依赖成环", ...).

A cycle is reported in the 前置 → 下游 direction and closed on itself, e.g.
``["T1", "T2", "T4", "T1"]`` for T1 depends_on [T4], T2 on [T1], T4 on [T2],
which is the exact body in examples/v2/scenario.json.
"""
import re

from .contracts import InvalidOutput, InvalidReference, PlanCycle

OUTPUT_KINDS = ("git_commit", "text")

_DRIVE = re.compile(r"^[A-Za-z]:")


def _fail(message, exc=InvalidReference, **details):
    raise exc(message, **details)


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _check_plan_shape(plan):
    if not isinstance(plan, dict):
        _fail("计划必须是对象", field="plan")
    if not _is_text(plan.get("id")):
        _fail("计划编号无效", field="id")
    if not _is_positive_int(plan.get("version")):
        _fail("计划版本必须为从 1 开始的整数", field="version")
    if not _is_positive_int(plan.get("max_parallel")):
        _fail("并发上限必须为正整数", field="max_parallel")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        _fail("任务不能为空", field="tasks")
    return tasks


def _check_spec_ref(plan, spec):
    ref = plan.get("spec_ref")
    if not isinstance(ref, dict):
        _fail("缺少规格引用", field="spec_ref")
    if not _is_text(ref.get("id")):
        _fail("规格引用缺少编号", field="spec_ref.id")
    if not _is_positive_int(ref.get("version")):
        _fail("规格引用版本无效", field="spec_ref.version")
    if not isinstance(spec, dict):
        return
    if _is_text(spec.get("id")) and spec["id"] != ref["id"]:
        _fail("计划引用了其他规格", field="spec_ref.id",
              expected=spec["id"], actual=ref["id"])
    if _is_positive_int(spec.get("version")) and spec["version"] != ref["version"]:
        _fail("计划引用了旧的规格版本", field="spec_ref.version",
              current_version=spec["version"], actual=ref["version"])


def _collect_ids(tasks):
    order, seen = [], set()
    for index, task in enumerate(tasks):
        where = f"tasks[{index}]"
        if not isinstance(task, dict):
            _fail("任务必须是对象", field=where)
        task_id = task.get("id")
        if not _is_text(task_id):
            _fail("任务编号无效", field=f"{where}.id")
        if task_id in seen:
            _fail("任务编号重复", field=f"{where}.id", task_id=task_id)
        seen.add(task_id)
        order.append(task_id)
    return seen, order


def _check_path(path, field, task_id):
    if not _is_text(path):
        _fail("路径不能为空", field=field, task_id=task_id)
    if "\\" in path:
        _fail("仓库内路径必须用 / 分隔", field=field, task_id=task_id, path=path)
    if path.startswith("/") or _DRIVE.match(path):
        _fail("不允许绝对路径", field=field, task_id=task_id, path=path)
    segments = [part for part in path.split("/") if part not in ("", ".")]
    if not segments:
        _fail("路径不能为空", field=field, task_id=task_id, path=path)
    if ".." in segments:
        _fail("路径不能包含 ..", field=field, task_id=task_id, path=path)


def _check_outputs(task, task_id, where):
    outputs = task.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise InvalidOutput("任务至少需要一项输出",
                            field=f"{where}.outputs", task_id=task_id)
    seen = set()
    for index, output in enumerate(outputs):
        at = f"{where}.outputs[{index}]"
        if not isinstance(output, dict):
            raise InvalidOutput("输出必须是对象", field=at, task_id=task_id)
        output_id = output.get("id")
        if not _is_text(output_id):
            raise InvalidOutput("输出编号无效", field=f"{at}.id", task_id=task_id)
        if output_id in seen:
            raise InvalidOutput("任务内输出编号重复", field=f"{at}.id",
                                task_id=task_id, output_id=output_id)
        seen.add(output_id)
        kind = output.get("kind")
        if kind not in OUTPUT_KINDS:
            raise InvalidOutput("输出类型必须为 git_commit 或 text",
                                field=f"{at}.kind", task_id=task_id, kind=kind)
        path = output.get("path")
        if path is None:
            if kind == "git_commit":
                raise InvalidOutput("提交产物必须给出路径",
                                    field=f"{at}.path", task_id=task_id)
        else:
            _check_path(path, f"{at}.path", task_id)


def _spec_ids(spec, key):
    """Ids available in the bound specification, or None when it carries none."""
    if not isinstance(spec, dict):
        return None
    items = spec.get(key)
    if not isinstance(items, list):
        return None
    return {item["id"] for item in items
            if isinstance(item, dict) and _is_text(item.get("id"))}


def _check_sources(task, task_id, where, spec):
    for key in ("requirement_ids", "decision_ids", "open_item_ids"):
        values = task.get(key)
        if not isinstance(values, list) or not all(_is_text(v) for v in values):
            _fail("来源引用必须是编号列表", field=f"{where}.{key}", task_id=task_id)
        if len(set(values)) != len(values):
            _fail("来源引用重复", field=f"{where}.{key}", task_id=task_id)
        allowed = _spec_ids(spec, {"requirement_ids": "requirements",
                                   "decision_ids": "decisions",
                                   "open_item_ids": "open_items"}[key])
        if allowed is None:
            continue
        unknown = [value for value in values if value not in allowed]
        if unknown:
            _fail("引用了不存在的条目", field=f"{where}.{key}",
                  task_id=task_id, unknown=unknown)


def _check_task(task, index, ids, spec):
    where = f"tasks[{index}]"
    task_id = task["id"]

    if not _is_text(task.get("title")):
        _fail("任务标题不能为空", field=f"{where}.title", task_id=task_id)
    if not _is_text(task.get("phase")):
        _fail("任务阶段不能为空", field=f"{where}.phase", task_id=task_id)
    if not _is_text(task.get("role")):
        _fail("执行角色不能为空", field=f"{where}.role", task_id=task_id)

    deps = task.get("depends_on")
    if not isinstance(deps, list) or not all(_is_text(dep) for dep in deps):
        _fail("前置任务必须是编号列表", field=f"{where}.depends_on", task_id=task_id)
    if len(set(deps)) != len(deps):
        _fail("前置任务重复", field=f"{where}.depends_on", task_id=task_id)
    if task_id in deps:
        _fail("任务不能依赖自身", field=f"{where}.depends_on", task_id=task_id)
    unknown = [dep for dep in deps if dep not in ids]
    if unknown:
        _fail("存在未知前置任务", field=f"{where}.depends_on",
              task_id=task_id, unknown=unknown)

    paths = task.get("write_paths")
    if not isinstance(paths, list):
        _fail("写入范围必须是列表", field=f"{where}.write_paths", task_id=task_id)
    for position, path in enumerate(paths):
        _check_path(path, f"{where}.write_paths[{position}]", task_id)
    if len({path.rstrip("/") for path in paths}) != len(paths):
        _fail("写入范围重复", field=f"{where}.write_paths", task_id=task_id)

    _check_outputs(task, task_id, where)

    if not _is_text(task.get("completion_condition")):
        raise InvalidOutput("任务必须有完成条件",
                            field=f"{where}.completion_condition", task_id=task_id)

    estimate = task.get("estimate_seconds")
    if estimate is not None and not _is_positive_int(estimate):
        _fail("估时必须是正整数或 null",
              field=f"{where}.estimate_seconds", task_id=task_id)

    if not isinstance(task.get("review_required"), bool):
        _fail("审核开关必须是布尔值",
              field=f"{where}.review_required", task_id=task_id)

    _check_sources(task, task_id, where, spec)


def _find_cycle(tasks, order):
    """Return the closed path 前置 → 下游, or None."""
    edges = {}
    for task in tasks:
        for dep in task["depends_on"]:
            edges.setdefault(dep, []).append(task["id"])

    state, stack = {}, []

    def walk(node):
        state[node] = 1
        stack.append(node)
        for following in edges.get(node, ()):
            if state.get(following) == 1:
                return stack[stack.index(following):] + [following]
            if state.get(following) is None:
                found = walk(following)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in order:
        if state.get(node) is None:
            found = walk(node)
            if found:
                return found
    return None


def overlaps(first, second):
    """True when two repository paths contend, comparing whole path segments.

    ``app`` and ``app2`` differ because the prefix is compared per segment.
    """
    if first == second:
        return True
    return (first.startswith(second.rstrip("/") + "/")
            or second.startswith(first.rstrip("/") + "/"))


def validate_plan(plan, confirmed_spec):
    """Validate a v2 plan against its confirmed specification.

    Returns None on success and raises PlanCycle / InvalidReference /
    InvalidOutput (all carrying ``code``, ``status`` and ``details``).
    """
    tasks = _check_plan_shape(plan)
    _check_spec_ref(plan, confirmed_spec)
    ids, order = _collect_ids(tasks)
    for index, task in enumerate(tasks):
        _check_task(task, index, ids, confirmed_spec)
    cycle = _find_cycle(tasks, order)
    if cycle:
        raise PlanCycle("任务依赖成环", cycle=cycle)
    return None