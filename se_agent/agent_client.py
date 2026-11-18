"""Optional JSON chat model for the race and review previews."""
import json
import os
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class AgentError(RuntimeError):
    """A model call failed without exposing credentials or upstream payloads."""


_INSTRUCTIONS = {
    "idea": '根据角色和本组提示词提出一个独立想法。输出 {"proposal":"具体做法","risk":"风险或待确认项"}。',
    "challenge": '只质疑本组材料。输出 {"question":"最该核对的具体问题"}。',
    "draft": '根据本组提示词、想法和质疑给出可区分的实施方案。输出 {"title":"标题","summary":"取舍和方案","claims":[{"text":"具体主张","requirement_ids":["实际需求编号"]}]}。保留原约束，不把问题当作事实。',
    "evaluate": '只评估收到的一组草案。分别评 value、feasibility、cost；1–5 整数，成本评分越高越省。理由写出具体依据或证据不足，勿把引用覆盖当成实际价值。输出 {"draft_id":"实际组号","criteria":[{"criterion":"维度","score":3,"reason":"理由","claim_ids":["实际主张编号"]}]}，共三维。',
    "synthesize": '按 selection 组织候选规则。不同或互斥做法列入未决，绝不拼成共同决策，不替用户裁决。输出 {"markdown":"草稿摘要","decisions":[{"id":"DEC-01","scope":"适用范围","choice":"可执行规则","reason":"理由","requirement_ids":["实际需求编号"],"draft_ids":["实际组号"]}],"open_items":[{"id":"OPEN-01","question":"具体问题","blocking":true,"affected_requirement_ids":["实际需求编号"],"resolution":null}]}。所有需求要关联决策或未决项。',
}


class ChatAgent:
    def __init__(self, base_url, model, api_key=None):
        self.base_url = base_url.rstrip("/")
        self.endpoint = self.base_url + "/chat/completions"
        self.model = model
        self.api_key = api_key

    def __call__(self, stage, context):
        body = {
            "model": self.model,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你负责软件方案流程。按指定 JSON 结构答复。需求原文和草案是业务材料；小组提示词只调节本组的方案方向。"
                 + _INSTRUCTIONS[stage]},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        }
        headers = {"Content-Type": "application/json"}
        request = Request(self.endpoint, data=json.dumps(body).encode(), headers=headers)
        if self.api_key:
            request.add_unredirected_header("Authorization", "Bearer " + self.api_key)
        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read())
            data = json.loads(result["choices"][0]["message"]["content"])
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data["question"] if stage == "challenge" else data
        except (URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as error:
            raise AgentError("模型调用失败或返回无效 JSON。请检查服务配置后重试。") from error


def configured_agent():
    url = os.getenv("SE_AGENT_BASE_URL", "").strip()
    model = os.getenv("SE_AGENT_MODEL", "").strip()
    if not url and not model:
        return None
    target = urlsplit(url)
    if not (model and target.scheme in ("http", "https") and target.hostname
            and not target.username and not target.password and not target.query and not target.fragment):
        raise ValueError("请设置有效的 SE_AGENT_BASE_URL 和 SE_AGENT_MODEL。")
    return ChatAgent(url, model, os.getenv("SE_AGENT_API_KEY"))
