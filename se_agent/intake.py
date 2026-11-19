"""Requirement intake and isolated proposal generation."""

import re
from copy import deepcopy

from .limits import MAX_INPUT_BYTES, MODEL_TIMEOUT_SECONDS

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_ROLES = {
    "A": ("产品范围", "工程实现"),
    "B": ("使用体验", "风险与运维"),
}
_ROUTE = {
    "A": ("核心路径", "收紧范围，先交付最小可用链路", "以核心规则为起点", "先把约束做成可验收的规则"),
    "B": ("体验替代", "围绕关键场景优化体验与异常处置", "以用户任务为起点", "先比较用户可见的处理方式"),
}
_DEFAULT_PROMPTS = {
    "A": "以最小可交付版本为目标。优先覆盖明确的核心规则，说明首版范围、实现顺序和验收点。把新增功能标为可选。",
    "B": "沿用户使用流程设计方案。优先说明状态提示、异常处理和替代路径，同时保留原有业务约束。",
}
EXTRA_GROUP_PROMPT = "从成本与交付风险出发，提出一条不同的路线。说明取舍、依赖和待验证假设，并引用需求编号。"


def default_group_prompts():
    return _DEFAULT_PROMPTS.copy()


def intake(markdown):
    if isinstance(markdown, str) and len(markdown.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("需求正文超过输入上限。")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("请输入需求文档")
    text = markdown.strip()
    return {"id": "REQ-01", "version": 1, "markdown": text}


def race(requirement):
    return [
        {"id": "A", "source": requirement["id"], "title": "先定义统一规则，再并行实现", "summary": "先固定接口和业务边界，再并行开发功能，最后集成。"},
        {"id": "B", "source": requirement["id"], "title": "先交付主链路，再补齐边界", "summary": "先实现最短端到端链路，再处理异常和交付说明。"},
    ]


def extract_items(markdown):
    """Extract Markdown blocks and preserve 1-based, inclusive line ranges."""
    if isinstance(markdown, str) and len(markdown.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("需求正文超过输入上限。")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("请输入需求 Markdown")
    items, lines = [], []
    section, start, end = "正文", 0, 0
    fence = None

    def flush():
        if not lines:
            return
        raw = "\n".join(lines)
        text = _ITEM.sub("", raw, count=1).strip()
        if text:
            items.append({
                "id": f"REQ-{len(items) + 1:02d}",
                "title": text.splitlines()[0][:40],
                "text": text,
                "source": {"section": section, "start_line": start, "end_line": end},
            })
        lines.clear()

    for number, line in enumerate(markdown.splitlines(), 1):
        marker = _FENCE.match(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            flush()
            fence = marker[1]
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            section = heading[1]
            continue
        if not line.strip() or re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            flush()
            continue
        if _ITEM.match(line):
            flush()
        if not lines:
            start = number
        end = number
        lines.append(line.rstrip())
    flush()
    if not items:
        raise ValueError("没有可提取的需求正文")
    return items


def build_snapshot(markdown, requirement_id, version):
    """Build a version supplied by the caller without rewriting the original."""
    if not isinstance(requirement_id, str) or not requirement_id or type(version) is not int or version < 1:
        raise ValueError("需求编号或版本无效")
    return {"id": requirement_id, "version": version, "markdown": markdown, "items": extract_items(markdown)}


def open_questions(requirement):
    """Identify explicit questions for the preview's review panel."""
    return [item for item in requirement["items"]
            if "澄清" in item["source"]["section"] or "待确认" in item["source"]["section"]
            or item["text"].rstrip().endswith(("？", "?"))]


def _mock_claim(item, group_id, pending):
    text = item["text"]
    if item["id"] in pending:
        return f"待确认，保留原问题：{text}"
    if group_id not in _ROUTE:
        return f"结合本组提示词评估实施选项，保留原需求约束：{text}"
    if "停用设备" in text and "预约" in text:
        if group_id == "A":
            return "预约接口校验设备状态。遇到停用设备直接拒绝创建预约。"
        return "设备详情显示停用原因或状态提示，隐藏新预约入口。服务端仍校验状态。"
    if "停用设备" in text and "列表" in text:
        if group_id == "A":
            return "设备查询继续返回停用记录，列表直接展示设备状态。"
        return "列表保留停用设备，提供显眼的状态标识，让用户能辨认仍可预约的设备。"
    method = _ROUTE[group_id][3]
    return f"{method}：{text}"


def _mock_agent(stage, context):
    group = context["group_id"]
    _, direction, opening, method = _ROUTE.get(group, (
        "自定义路线", f"{group} 组自定义路线", "根据本组提示词比较实施选项", "列出取舍及待验证假设"
    ))
    if stage == "idea":
        role = context["role"]
        focus = {
            "产品范围": "先用以下条目界定首版范围",
            "工程实现": "把以下条目转成能验证的实现规则",
            "使用体验": "把以下条目放进用户主路径，明确反馈",
            "风险与运维": "对以下条目列异常分支与处理责任",
            "方案探索": "围绕本组提示词为以下条目提出候选方向",
            "可行性复核": "检查候选方向对以下条目的约束和验证方式",
        }[role]
        questions = open_questions(context["requirement"])
        refs = "、".join(item["id"] for item in context["requirement"]["items"])
        risk = "待澄清 " + "、".join(item["id"] for item in questions) if questions else "输入没有说明的例外需要复核。"
        return {"proposal": focus + " " + refs + "。", "risk": risk}
    if stage == "challenge":
        return "核对方案是否覆盖全部已确认条目，并把未说明的例外列为问题。"
    pending = {item["id"] for item in open_questions(context["requirement"])}
    if group not in _DEFAULT_PROMPTS or context["group_prompt"] != _DEFAULT_PROMPTS[group]:
        method += "。本组自定义提示词：" + context["group_prompt"]
    return {
        "title": direction,
        "summary": f"{opening}。{method}。方案以同一需求快照为依据。",
        "claims": [
            {"text": _mock_claim(item, group, pending),
             "requirement_ids": [item["id"]]}
            for item in context["requirement"]["items"]
        ],
    }


def run_group(requirement, group_id, *, prompt=None, agent=_mock_agent, progress=None):
    """Run isolated ideas, one group challenge, then a contract-ready draft.

    The optional adapter receives (stage, context) and returns the same shapes
    as the local mock. Each call receives a fresh copy of its allowed context.
    """
    if not isinstance(group_id, str) or not re.fullmatch(r"[A-Z]+", group_id):
        raise ValueError("组别须为大写字母编号")
    if prompt is None:
        prompt = _DEFAULT_PROMPTS.get(group_id)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"请输入 {group_id} 组提示词")
    prompt = prompt.strip()
    known = {item["id"] for item in requirement["items"]}
    if not known:
        raise ValueError("需求快照为空")
    context = {"group_id": group_id, "group_prompt": prompt, "requirement": requirement}
    ideas = []
    for role in _ROLES.get(group_id, ("方案探索", "可行性复核")):
        if progress:
            progress(f"{group_id} 组：{role}独立构思", MODEL_TIMEOUT_SECONDS if agent != _mock_agent else 0)
        result = agent("idea", deepcopy({**context, "role": role}))
        if not isinstance(result, dict) or not all(isinstance(result.get(k), str) and result[k].strip() for k in ("proposal", "risk")):
            raise ValueError("独立想法缺少主张或风险")
        ideas.append({"role": role, "proposal": result["proposal"], "risk": result["risk"]})
    if progress:
        progress(f"{group_id} 组：检查方案风险", MODEL_TIMEOUT_SECONDS if agent != _mock_agent else 0)
    challenge = agent("challenge", deepcopy({**context, "ideas": ideas}))
    if not isinstance(challenge, str) or not challenge.strip():
        raise ValueError("组内质疑不能为空")
    if progress:
        progress(f"{group_id} 组：整理草案", MODEL_TIMEOUT_SECONDS if agent != _mock_agent else 0)
    proposal = agent("draft", deepcopy({**context, "ideas": ideas, "challenge": challenge}))
    if not isinstance(proposal, dict) or not all(isinstance(proposal.get(k), str) and proposal[k].strip() for k in ("title", "summary")):
        raise ValueError("草案缺少标题或摘要")
    candidates = proposal.get("claims")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("草案缺少主张")
    claims = []
    for i, claim in enumerate(candidates, 1):
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str) or not claim["text"].strip():
            raise ValueError("草案主张不能为空")
        refs = claim.get("requirement_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in known for ref in refs):
            raise ValueError("草案引用了未登记的需求")
        claims.append({"id": f"{group_id}-{i:02d}", "text": claim["text"], "requirement_ids": list(dict.fromkeys(refs))})
    draft = {"id": group_id, "requirement_ref": {"id": requirement["id"], "version": requirement["version"]},
             "title": proposal["title"], "summary": proposal["summary"], "claims": claims}
    return {"draft": draft, "ideas": ideas, "challenge": challenge}


def generate_draft(requirement, group_id, *, prompt=None):
    """Public v2 module entry point; read only the supplied snapshot."""
    return run_group(requirement, group_id, prompt=prompt)["draft"]
