"""Proposal assessment, synthesis and explicit confirmation."""
from .contracts import Conflict
from copy import deepcopy
import re


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


class SynthesisError(ValueError):
    """A local v2 validation error, without changing the shared v1 contract."""

    def __init__(self, message, path="", code="INVALID_REFERENCE"):
        super().__init__(message)
        self.code, self.path = code, path


def _check(condition, message, path="", code="INVALID_REFERENCE"):
    if not condition:
        raise SynthesisError(message, path, code)


def _text(value, path):
    _check(isinstance(value, str) and bool(value.strip()), "必须是非空文本", path)


def _object(value, path):
    _check(isinstance(value, dict), "必须是对象", path)


def _rows(value, path, nonempty=False):
    _check(isinstance(value, list) and (bool(value) or not nonempty), "必须是列表" + ("且不能为空" if nonempty else ""), path)


def _unique(rows, path):
    ids = []
    for i, row in enumerate(rows):
        _object(row, f"{path}[{i}]")
        _text(row.get("id"), f"{path}[{i}].id")
        ids.append(row["id"])
    _check(len(ids) == len(set(ids)), "编号重复", path)
    return set(ids)


def _refs(value, known, path, nonempty=True):
    _rows(value, path, nonempty)
    _check(all(isinstance(ref, str) and ref in known for ref in value), "引用了不存在的编号", path)
    _check(len(value) == len(set(value)), "引用编号重复", path)


def validate_requirement(requirement):
    _object(requirement, "requirement")
    _text(requirement.get("id"), "requirement.id")
    _check(type(requirement.get("version")) is int and requirement["version"] > 0, "版本必须是正整数", "requirement.version")
    _text(requirement.get("markdown"), "requirement.markdown")
    items = requirement.get("items")
    _rows(items, "requirement.items", True)
    known = _unique(items, "requirement.items")
    line_count = len(requirement["markdown"].splitlines())
    for i, item in enumerate(items):
        path = f"requirement.items[{i}]"
        for key in ("title", "text"):
            _text(item.get(key), f"{path}.{key}")
        source = item.get("source")
        _object(source, path + ".source")
        _text(source.get("section"), path + ".source.section")
        start, end = source.get("start_line"), source.get("end_line")
        _check(type(start) is int and type(end) is int and 1 <= start <= end <= line_count,
               "来源行号超出原文范围", path + ".source")
    return known


def validate_draft(requirement, draft):
    known = validate_requirement(requirement)
    _object(draft, "draft")
    _check(isinstance(draft.get("id"), str) and re.fullmatch(r"[A-Z]+", draft["id"]),
           "草案编号须为大写字母", "draft.id")
    ref = draft.get("requirement_ref")
    _object(ref, "draft.requirement_ref")
    _check(type(ref.get("version")) is int and ref == {"id": requirement["id"], "version": requirement["version"]},
           "草案与需求快照版本不一致", "draft.requirement_ref")
    for key in ("title", "summary"):
        _text(draft.get(key), "draft." + key)
    _rows(draft.get("claims"), "draft.claims", True)
    claims = _unique(draft["claims"], "draft.claims")
    for i, claim in enumerate(draft["claims"]):
        _text(claim.get("text"), f"draft.claims[{i}].text")
        _refs(claim.get("requirement_ids"), known, f"draft.claims[{i}].requirement_ids")
    return claims


def validate_handoff(requirement, drafts):
    validate_requirement(requirement)
    _rows(drafts, "drafts", True)
    _unique(drafts, "drafts")
    _check(len(drafts) >= 2, "至少需要两份不同小组的草案", "drafts")
    seen = set()
    for draft in drafts:
        claims = validate_draft(requirement, draft)
        _check(not seen.intersection(claims), "两组主张编号重复", "drafts.claims")
        seen.update(claims)


def validate_evaluation(requirement, draft, evaluation):
    claims = validate_draft(requirement, draft)
    _object(evaluation, "evaluation")
    _check(evaluation.get("draft_id") == draft["id"], "评价对象与草案不一致", "evaluation.draft_id")
    criteria = evaluation.get("criteria")
    _rows(criteria, "evaluation.criteria", True)
    names = []
    for i, criterion in enumerate(criteria):
        path = f"evaluation.criteria[{i}]"
        _object(criterion, path)
        names.append(criterion.get("criterion"))
        _check(type(criterion.get("score")) is int and 1 <= criterion["score"] <= 5, "评分须为 1～5 的整数", path + ".score")
        _text(criterion.get("reason"), path + ".reason")
        _refs(criterion.get("claim_ids"), claims, path + ".claim_ids")
    _check(len(names) == 3 and all(isinstance(n, str) for n in names) and set(names) == {"value", "feasibility", "cost"},
           "必须且只能包含 value、feasibility、cost 三项", "evaluation.criteria")


def validate_proposal(requirement, drafts, proposal):
    """Also validate human edits; semantic agreement remains a human review."""
    validate_handoff(requirement, drafts)
    _object(proposal, "proposal")
    _text(proposal.get("markdown"), "proposal.markdown")
    known = {item["id"] for item in requirement["items"]}
    covered = set()
    group_ids = {draft["id"] for draft in drafts}
    claims = {claim["id"]: (draft["id"], claim) for draft in drafts for claim in draft["claims"]}
    for key in ("decisions", "open_items"):
        _rows(proposal.get(key), "proposal." + key)
        _unique(proposal[key], "proposal." + key)
    for i, decision in enumerate(proposal["decisions"]):
        path = f"proposal.decisions[{i}]"
        for key in ("scope", "choice", "reason"):
            _text(decision.get(key), path + "." + key)
        _refs(decision.get("requirement_ids"), known, path + ".requirement_ids")
        _refs(decision.get("draft_ids"), group_ids, path + ".draft_ids")
        covered.update(decision["requirement_ids"])
    for i, item in enumerate(proposal["open_items"]):
        path = f"proposal.open_items[{i}]"
        _text(item.get("question"), path + ".question")
        _check(type(item.get("blocking")) is bool, "blocking 必须是布尔值", path + ".blocking")
        _check("resolution" in item, "缺少 resolution（未解决时为 null）", path + ".resolution")
        _refs(item.get("affected_requirement_ids"), known, path + ".affected_requirement_ids")
        affected = set(item["affected_requirement_ids"])
        options = item.get("options", [])
        _rows(options, path + ".options")
        for option in options:
            _object(option, path + ".options")
            key = option.get("claim_id")
            _check(isinstance(key, str) and key in claims, "选项引用了未知主张", path + ".options")
            group, claim = claims[key]
            _check(option.get("draft_id") == group and option.get("text") == claim["text"]
                   and affected.intersection(claim["requirement_ids"]),
                   "候选选项与原始草案不一致", path + ".options")
        if item["resolution"] is None:
            _check(not item.get("decision_id"), "请撤销已关联的取舍后再保留未决。",
                   path + ".decision_id", "RESOLUTION_NOT_APPLIED")
        else:
            _text(item["resolution"], path + ".resolution")
            linked = item.get("decision_id")
            _check(any(d["choice"].strip() == item["resolution"].strip()
                       and affected <= set(d["requirement_ids"])
                       and (linked is None or d["id"] == linked)
                       for d in proposal["decisions"]),
                   "请先把 "+item["id"]+" 的人工答复写入决策，再保存或确认。",
                   path + ".resolution", "RESOLUTION_NOT_APPLIED")
        covered.update(affected)
    _check(covered == known, "部分需求未关联决策或未决事项：" + "、".join(sorted(known - covered)), "proposal.decisions", "INVALID_OUTPUT")


def _cancellation_suggestion(requirement, claim):
    """Narrow demonstration rule, not a general semantic entailment checker."""
    return ("自动取消" in claim["text"] and "预约" in claim["text"]
            and "取消" not in "\n".join(item["text"] for item in requirement["items"]))


def render_spec_markdown(requirement, decisions, open_items, notes=""):
    parts = ["# 综合开发方案", "## 需求原文与约束", requirement["markdown"], "## 方案决策"]
    for row in decisions:
        parts.append(f"### {row['id']} · {row['scope']}\n{row['choice']}\n\n理由：{row['reason']}\n\n来源："
                     + "、".join(row["requirement_ids"] + row["draft_ids"]))
    parts.append("## 待处理事项")
    for row in open_items:
        parts.append(f"- {row['id']} · {row['question']}\n  影响：{'、'.join(row['affected_requirement_ids'])}；"
                     + ("阻塞相关任务" if row["blocking"] else "非阻塞")
                     + "；处理结果：" + (row["resolution"] or "待人工决定"))
        if row["resolution"] is None:
            parts.extend(f"  - {option['claim_id']} · {option['text']}" for option in row.get("options", []))
    if not open_items:
        parts.append("无已登记的未决事项，仍需人工复查业务语义。")
    if notes.strip():
        parts.extend(["## 人工补充说明", notes.strip()])
    return "\n\n".join(parts)


def mock_agent(stage, context):
    """Offline adapter: source coverage, neutral estimates and one demo rule."""
    requirement = context["requirement"]
    if stage == "evaluate":
        draft = context["draft"]
        covered = {ref for claim in draft["claims"] for ref in claim["requirement_ids"]}
        refs = [claim["id"] for claim in draft["claims"]]
        return {"draft_id": draft["id"], "criteria": [
            {"criterion": "value", "score": 1 + round(4 * len(covered) / len(requirement["items"])),
             "reason": f"模拟：主张引用覆盖 {len(covered)}/{len(requirement['items'])} 项需求，仅衡量来源覆盖，不代表真实用户价值。", "claim_ids": refs},
            {"criterion": "feasibility", "score": 3,
             "reason": "模拟中性值：尚未核实技术和外部依赖，须人工复查。", "claim_ids": refs},
            {"criterion": "cost", "score": 3,
             "reason": "模拟中性值：缺少可信工时和预算依据，不能据此断言更省。", "claim_ids": refs}]}
    _check(stage == "synthesize", "不支持的 Agent 阶段", "stage")
    selected = [d for d in context["drafts"] if context["selection"] == "merge" or d["id"] == context["selection"]]
    decisions, open_items = [], []

    def pending(item, question, candidates=()):
        row = {"id": f"OPEN-{len(open_items) + 1:02d}", "question": question, "blocking": True,
               "affected_requirement_ids": [item["id"]], "resolution": None}
        if candidates:
            row["options"] = [{"claim_id": c["id"], "draft_id": group, "text": c["text"]}
                              for group, c in candidates]
        open_items.append(row)

    for item in requirement["items"]:
        candidates = [(d["id"], c) for d in selected for c in d["claims"] if item["id"] in c["requirement_ids"]]
        if re.search(r"[？?]\s*$", item["text"]) or any(word in item["source"]["section"] for word in ("待澄清", "待确认")):
            pending(item, item["text"])
            continue
        unsafe = [(group, c) for group, c in candidates if _cancellation_suggestion(requirement, c)]
        if unsafe:
            pending(item, "示例复查：" + "、".join(c["id"] for _, c in unsafe)
                    + "提出自动取消已有预约，原需求未确认该规则。请选择保留仍可履约的预约并处理改期、取消受影响预约并告知用户，或暂缓决定；补充理由。", unsafe)
        safe = [(group, c) for group, c in candidates if not _cancellation_suggestion(requirement, c)]
        if not safe:
            if not unsafe:
                pending(item, "所选草案尚未覆盖此需求，请补充实现规则：" + item["text"])
            continue
        alternatives = [(d["id"], c) for d in context["drafts"] for c in d["claims"]
                        if item["id"] in c["requirement_ids"] and not _cancellation_suggestion(requirement, c)]
        if len({c["text"] for _, c in alternatives}) > 1 and len({g for g, _ in alternatives}) > 1:
            pending(item, "多组对该需求提出了不同做法。请核对依据，选择或改写最终规则。", alternatives)
            continue
        texts = list(dict.fromkeys(c["text"] for _, c in safe))
        decisions.append({"id": f"DEC-{len(decisions) + 1:02d}", "scope": item["title"],
                          "choice": "\n".join(texts), "reason": "模拟整合：按人工选择整理候选主张 "
                          + "、".join(c["id"] for _, c in safe) + "，未自动判定语义一致性；确认前需人工复查。",
                          "requirement_ids": [item["id"]], "draft_ids": list(dict.fromkeys(group for group, _ in safe))})
    return {"markdown": render_spec_markdown(requirement, decisions, open_items), "decisions": decisions, "open_items": open_items}


def evaluate_draft(requirement, draft, *, agent=mock_agent):
    """Evaluate exactly one draft with an isolated, replaceable Agent context."""
    validate_draft(requirement, draft)
    result = agent("evaluate", deepcopy({"requirement": requirement, "draft": draft}))
    validate_evaluation(requirement, draft, result)
    return deepcopy(result)


def propose_spec(requirement, drafts, evaluations, selection, *, agent=mock_agent):
    """Return content only; the calling service owns identity and confirmation."""
    validate_handoff(requirement, drafts)
    group_ids = {d["id"] for d in drafts}
    _check(isinstance(selection, str) and (selection == "merge" or selection in group_ids),
           "请选择已参赛的小组或融合", "selection")
    _rows(evaluations, "evaluations", True)
    _check(len(evaluations) == len(drafts) and all(isinstance(e, dict) for e in evaluations),
           "每份草案都需要一份独立评价", "evaluations")
    _check({e.get("draft_id") for e in evaluations if isinstance(e.get("draft_id"), str)} == group_ids,
           "评价对象须与参赛草案一致", "evaluations")
    for draft in drafts:
        validate_evaluation(requirement, draft, next(e for e in evaluations if e["draft_id"] == draft["id"]))
    context = {"requirement": requirement, "drafts": drafts, "evaluations": evaluations, "selection": selection}
    result = deepcopy(agent("synthesize", deepcopy(context)))
    validate_proposal(requirement, drafts, result)
    if agent is not mock_agent:
        _check(all(row["resolution"] is None for row in result["open_items"]),
               "模型不能替人工处置未决事项", "proposal.open_items", "INVALID_OUTPUT")
        guard = mock_agent("synthesize", context)
        for issue in guard["open_items"]:
            if "options" not in issue:
                continue
            affected = set(issue["affected_requirement_ids"])
            covered_options = {o["claim_id"] for o in issue["options"]}
            result["decisions"] = [d for d in result["decisions"]
                                   if not affected.intersection(d["requirement_ids"])]
            if not any(covered_options <= {o["claim_id"] for o in row.get("options", [])}
                       and row["resolution"] is None for row in result["open_items"]):
                used = {row["id"] for row in result["open_items"]}
                number = next(n for n in range(1, len(used) + 2) if f"OPEN-{n:02d}" not in used)
                result["open_items"].append({**issue, "id": f"OPEN-{number:02d}"})
    result["markdown"] = render_spec_markdown(requirement, result["decisions"], result["open_items"])
    validate_proposal(requirement, drafts, result)
    return {key: deepcopy(result[key]) for key in ("markdown", "decisions", "open_items")}
