"""In-memory bridge from an HSX-confirmed specification to the QLM planner."""
from copy import deepcopy
from graphlib import CycleError, TopologicalSorter
import re

from .planner import decompose_tasks, render_plan_markdown
from .synthesis_preview import PreviewError


class PlanPreviewStore:
    def __init__(self):
        self.plans = {}

    @staticmethod
    def _response(review_id, plan):
        return {"review_id": review_id, "plan": deepcopy(plan), "task_markdown": render_plan_markdown(plan)}

    def generate(self, review, version):
        spec = review["specification"]
        if (type(version) is not int or not spec or spec["status"] != "confirmed" or spec["version"] != version):
            raise PreviewError("INVALID_STATE", "请先确认当前综合方案。")
        review_id = review["id"]
        ref = {"id": spec["id"], "version": spec["version"]}
        previous = self.plans.get(review_id)
        if previous and previous["spec_ref"] == ref:
            return self._response(review_id, previous)
        items = review["requirement"]["items"]
        tasks = decompose_tasks({**spec, "requirement_items": items})
        plan = {"id": "PLAN-" + review_id[:8], "version": previous["version"] + 1 if previous else 1,
                "spec_ref": ref, "base_commit": None, "max_parallel": 2, "tasks": tasks}
        self.plans[review_id] = plan
        return self._response(review_id, plan)

    def save(self, review, expected_version, tasks):
        review_id = review["id"]
        plan = self.plans.get(review_id)
        if not plan:
            raise PreviewError("NOT_FOUND", "请先生成任务分解。", 404)
        spec = review["specification"]
        if (not spec or spec["status"] != "confirmed" or
                plan["spec_ref"] != {"id": spec["id"], "version": spec["version"]}):
            raise PreviewError("STALE_SPEC", "评审规格已变化，请重新交接。")
        if type(expected_version) is not int or expected_version != plan["version"]:
            raise PreviewError("VERSION_CONFLICT", "计划版本已变化。", current_version=plan["version"])
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("任务列表不能为空")
        originals = {task["id"]: task for task in plan["tasks"]}
        edited = {}
        known = {item["id"] for item in review["requirement"]["items"]}
        for row in tasks:
            if not isinstance(row, dict):
                raise ValueError("任务必须为对象")
            id_ = row.get("id")
            if not isinstance(id_, str) or not re.fullmatch(r"T\d+(?:\.\d+)?", id_) or id_ in edited:
                raise ValueError("任务编号格式无效或重复")
            title, role = row.get("title"), row.get("role")
            estimate, predecessors = row.get("estimate_seconds"), row.get("depends_on")
            if not all(isinstance(value, str) and value.strip() for value in (title, role)):
                raise ValueError(f"{id_} 的标题和执行角色不能为空")
            if type(estimate) is not int or estimate <= 0:
                raise ValueError(f"{id_} 的估时应为正整数秒")
            if not isinstance(predecessors, list) or not all(isinstance(value, str) for value in predecessors):
                raise ValueError(f"{id_} 的前置任务应为编号列表")
            if len(set(predecessors)) != len(predecessors):
                raise ValueError(f"{id_} 有重复前置任务")
            review_required = row.get("review_required")
            if type(review_required) is not bool:
                raise ValueError(f"{id_} 的人工审核应为布尔值")
            task = originals.get(id_)
            if task is None:
                refs = row.get("requirement_ids")
                if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r in known for r in refs):
                    raise ValueError(f"{id_} 需要关联当前规格的需求编号")
                path = f"docs/custom/{id_.lower()}.md"
                task = {"id": id_, "phase": "development", "requirement_ids": list(dict.fromkeys(refs)),
                        "decision_ids": [d["id"] for d in spec["decisions"] if set(refs) & set(d["requirement_ids"])],
                        "open_item_ids": [o["id"] for o in spec["open_items"] if o["blocking"]
                                          and o["resolution"] is None and set(refs) & set(o["affected_requirement_ids"])],
                        "write_paths": [path], "outputs": [{"id": "OUT-01", "kind": "text", "path": path}],
                        "completion_condition": "按关联需求核对并记录成果。"}
            edited[id_] = {**task, "title": title.strip(), "role": role.strip(), "depends_on": predecessors,
                           "estimate_seconds": estimate, "review_required": review_required}
        if originals.keys() - edited.keys():
            raise ValueError("不能删除已分解任务")
        graph = {id_: task["depends_on"] for id_, task in edited.items()}
        for id_, deps in graph.items():
            if set(deps) - edited.keys():
                raise ValueError(f"{id_} 引用了不存在的前置任务")
        try:
            tuple(TopologicalSorter(graph).static_order())
        except CycleError as error:
            raise PreviewError("PLAN_CYCLE", "任务依赖成环", 422, cycle=error.args[1]) from error
        revised = {**plan, "version": plan["version"] + 1, "tasks": list(edited.values())}
        self.plans[review_id] = revised
        return self._response(review_id, revised)
