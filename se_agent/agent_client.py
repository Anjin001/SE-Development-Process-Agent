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
    "steward_groups": '你是项目主理人。根据需求和流程偏好，为同一需求选择 2 至 5 个并行路线；输入指定 count 时必须遵守。每组的关注点、取舍和提示词要实质不同，读同一份需求。只返回 {"groups":[{"focus":"角度","prompt":"该组的差异化工作说明"}]}。',
    "steward_decisions": '你是本地软件原型主理人。替用户做本轮可撤销的产品与技术默认决定，不要求用户逐项回复。不改变明确需求；遇到缺信息选有利于最小可用版本、权限最小或关闭扩展的默认，不能假称用户授权了付费、外发数据、上线或删除已有真实数据。每个 issues 中的 id 恰好出现一次，给出具体做法及短理由，保证没有互斥决定。返回 {"decisions":[{"id":"原编号","choice":"一句可执行规则","reason":"简述取舍"}]}。',
    "steward_plan": '为同一个确认版需求设计可并发的功能工作包。一个需求编号可以覆盖多个功能，不允许把多个可分功能塞进一个包。公共 API、数据与分支约定由单独交接任务完成；模块之间尽可能不抢写文件。覆盖所有需求，至少 minimum_modules 个模块。只返回 {"modules":[{"slug":"notes","title":"图文发布","scope":"具体职责和边界","requirement_ids":["REQ-01"],"kind":"service","minutes":15}]}。slug 用小写字母数字和下划线，kind 只能为 service 或 web。',
    "steward_review": '你在复核本地软件开发产物。依据任务完成条件、范围与看到的内容做自动判断。files 中的 excerpt 是逐文件 diff 摘录。excerpt_is_partial 为 true 表示展示省略，不能据此判断源文件被截断或语法错误。只检查可见证据，不声称运行过测试。实际为空、明显违反要求或不完整时退回。返回 {"approved":true,"reason":"一句可核对的理由"}。',
    "steward_chat": '你是项目主理人。判断用户这条消息是咨询，还是明确要求调整需求、暂停、继续、重试、重跑或查进度。含糊不清只回答，绝不擅自改需求。尤其是追问，结合对话历史和失败证据，把原因与下一步说具体，不复读状态；缺证据时说明不知道。返回 {"intent":"ask|revise|pause|continue|retry|redo|status","reply":"两句以内的答复"}。',
    "help": '你是此软件工作台的使用答疑员。按提供的使用说明与状态回答，不能执行操作，不能编造按钮、进度或文件位置。无法判断就直接说明。最多三句中文，返回 {"answer":"回答"}。',
    "idea": '根据角色和本组提示词提出一个独立想法。输出 {"proposal":"具体做法","risk":"风险或待确认项"}。',
    "challenge": '只质疑本组材料。输出 {"question":"最该核对的具体问题"}。',
    "draft": '根据本组提示词、想法和质疑给出可区分的实施方案。输出 {"title":"标题","summary":"取舍和方案","claims":[{"text":"具体主张","requirement_ids":["实际需求编号"]}]}。保留原约束，不把问题当作事实。',
    "evaluate": '只评估收到的一组草案。分别评 value、feasibility、cost；1–5 整数，成本评分越高越省。理由写出具体依据或证据不足，勿把引用覆盖当成实际价值。输出 {"draft_id":"实际组号","criteria":[{"criterion":"维度","score":3,"reason":"理由","claim_ids":["实际主张编号"]}]}，共三维。',
    "synthesize": '按 selection 组织候选规则。不同或互斥的产品规则列入未决，绝不拼成共同决定。普通实现细节写成可逆的原型决定。只把确实缺少且会改变范围、权限、隐私、费用或主要用户行为的选择标为阻塞。每项只用一句短问句，点明功能和要选择的行为。输出 {"markdown":"草稿摘要","decisions":[{"id":"DEC-01","scope":"适用范围","choice":"可执行规则","reason":"理由","requirement_ids":["实际需求编号"],"draft_ids":["实际组号"]}],"open_items":[{"id":"OPEN-01","question":"具体问题","blocking":true,"affected_requirement_ids":["实际需求编号"],"resolution":null}]}。所有需求要关联决策或未决项。',
}

_STAGE_NAMES = {
    "steward_groups": "设计赛马路线",
    "steward_decisions": "主理人补齐决定",
    "steward_plan": "主理人安排任务",
    "steward_review": "主理人复核产物",
    "steward_chat": "主理人理解指令",
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
