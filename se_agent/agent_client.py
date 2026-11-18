"""Optional JSON chat model for the race and review previews."""
import json
import os
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class AgentError(RuntimeError):
    """A model call failed without exposing credentials or upstream payloads."""


_INSTRUCTIONS = {
    "help": '你是此软件工作台的使用答疑员。按提供的使用说明与状态回答，不能执行操作，不能编造按钮、进度或文件位置。无法判断就直接说明。最多三句中文，返回 {"answer":"回答"}。',
    "idea": '根据角色和本组提示词提出一个独立想法。输出 {"proposal":"具体做法","risk":"风险或待确认项"}。',
    "challenge": '只质疑本组材料。输出 {"question":"最该核对的具体问题"}。',
    "draft": '根据本组提示词、想法和质疑给出可区分的实施方案。输出 {"title":"标题","summary":"取舍和方案","claims":[{"text":"具体主张","requirement_ids":["实际需求编号"]}]}。保留原约束，不把问题当作事实。',
    "evaluate": '只评估收到的一组草案。分别评 value、feasibility、cost；1–5 整数，成本评分越高越省。理由写出具体依据或证据不足，勿把引用覆盖当成实际价值。输出 {"draft_id":"实际组号","criteria":[{"criterion":"维度","score":3,"reason":"理由","claim_ids":["实际主张编号"]}]}，共三维。',
    "synthesize": '按 selection 组织候选规则。不同或互斥的产品规则列入未决，绝不拼成共同决定。普通实现细节写成可逆的原型决定。只把确实缺少且会改变范围、权限、隐私、费用或主要用户行为的选择标为阻塞。每项只用一句短问句，点明功能和要选择的行为。输出 {"markdown":"草稿摘要","decisions":[{"id":"DEC-01","scope":"适用范围","choice":"可执行规则","reason":"理由","requirement_ids":["实际需求编号"],"draft_ids":["实际组号"]}],"open_items":[{"id":"OPEN-01","question":"具体问题","blocking":true,"affected_requirement_ids":["实际需求编号"],"resolution":null}]}。所有需求要关联决策或未决项。',
}

_STAGE_NAMES = {
    "help": "工作台答疑",
    "idea": "独立构思",
    "challenge": "组内质疑",
    "draft": "生成草案",
    "evaluate": "独立评审",
    "synthesize": "整合方案",
}

_HTTP_GUIDANCE = {
    400: "模型服务拒绝了请求，请检查该模型是否支持 JSON 输出及输入长度。",
    401: "模型服务鉴权失败，请检查 API Key。",
    403: "当前 API Key 没有调用此模型的权限。",
    404: "模型或请求地址不存在，请核对模型 ID 和 API 基础地址。",
    429: "模型服务限流或额度不足，请稍后重试并检查用量。",
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
        stage_name = _STAGE_NAMES[stage]
        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            status = error.code
            error.close()
            message = _HTTP_GUIDANCE.get(status)
            if not message:
                message = ("模型服务暂时不可用，请稍后重试。" if status >= 500
                           else "模型服务拒绝了请求，请核对接口配置。")
            raise AgentError(f"{stage_name}失败（HTTP {status}）：{message}") from None
        except (HTTPException, OSError) as error:
            timed_out = (isinstance(error, TimeoutError)
                         or isinstance(getattr(error, "reason", None), TimeoutError))
            message = ("模型请求超时，请重试该组或缩短输入。" if timed_out
                       else "无法连接模型服务，请检查网络和 API 基础地址。")
            raise AgentError(f"{stage_name}失败：{message}") from None
        except ValueError:
            raise AgentError(f"{stage_name}失败：模型接口未返回有效 JSON。") from None
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise AgentError(f"{stage_name}失败：模型输出被截断，请缩短输入或调整模型输出上限。")
            if choice["message"].get("refusal"):
                raise AgentError(f"{stage_name}失败：模型拒绝生成此内容，请检查输入和提示词。")
            data = json.loads(choice["message"]["content"])
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data["question"] if stage == "challenge" else data
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AgentError(f"{stage_name}失败：模型回复未返回要求的 JSON 结构，请重试该组或更换模型。") from None


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
