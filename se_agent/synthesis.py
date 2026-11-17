"""Proposal assessment, synthesis and explicit confirmation."""
from .contracts import Conflict


def assess(drafts):
    return [
        {"draft_id": draft["id"], "value": 4, "feasibility": 4 if draft["id"] == "A" else 3,
         "cost": 3 if draft["id"] == "A" else 4,
         "reason": "统一接口便于并行" if draft["id"] == "A" else "先交付主链路可以缩短反馈周期"}
        for draft in drafts
    ]


def confirm(requirement, drafts, decision):
    if decision not in {"A", "B", "merge"}:
        raise ValueError("请选择 A、B 或融合")
    selected = [d for d in drafts if decision == "merge" or d["id"] == decision]
    if not selected:
        raise Conflict("缺少对应草案")
    strategy = ("采用 A 的统一接口约定和 B 的主链路优先策略。" if decision == "merge"
                else selected[0]["summary"])
    return {"id": "SPEC-01", "version": 1, "requirement_id": requirement["id"],
            "source_draft_ids": [d["id"] for d in selected],
            "markdown": requirement["markdown"] + "\n\n## 实施策略\n" + strategy}
