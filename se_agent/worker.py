"""v2 执行入口。

接 WorkerRequest，回 WorkerResult，见 docs/接口契约-v2.md §5。

模拟链路不触碰 Git 和模型，用来让编排器先把联调跑通；真实链路在服务端配置的
仓库上开独立 worktree，先纳入已批准的上游提交并保留起点，再由模型路由驱动的
执行器改文件、提交，回报 Git SHA 与用量。执行器默认走提示词骨架，
AutoGen 0.2 适配通过 executor="autogen" 打开。
"""
import ast
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .api_contract import CONTRACT_PATH, parse_endpoints, view_for_file
from .model_router import ModelRouter, load_config
from .providers import (LLMResult, OpenAICompatibleProvider, ProviderError, ProviderOutputError,
                        ProviderTimeout, build_provider, estimate_tokens)
from .worktree import MergeConflict, WorkspaceError, WorktreeAdapter

# 契约 §5 约定的错误码
WORKER_TIMEOUT = "WORKER_TIMEOUT"
MERGE_CONFLICT = "MERGE_CONFLICT"
MISSING_INPUT = "MISSING_INPUT"
EXECUTION_FAILED = "EXECUTION_FAILED"
OUTPUT_INVALID = "OUTPUT_INVALID"

RETRYABLE = {WORKER_TIMEOUT: True, MERGE_CONFLICT: True, EXECUTION_FAILED: True,
             MISSING_INPUT: False, OUTPUT_INVALID: False}

SYSTEM_PROMPT = "你是开发执行器。只输出要写入目标文件的内容，不要解释，不要 Markdown 代码围栏。"


class FileResponseError(WorkspaceError):
    def __init__(self,path,cause):
        super().__init__(f"{path}：{cause}；未提交不完整文件")
        self.details = dict(path=path,finish_reason=cause.finish_reason,max_tokens=cause.max_tokens,usage=cause.usage)


class OutputContentError(WorkspaceError):
    def __init__(self, path, detail):
        super().__init__(f"{path}：{detail}")
        self.path = path


def checked_content(text, path, kind=None):
    if not isinstance(text,str) or not text.strip():
        raise OutputContentError(path, "模型没有返回文件正文，未写文件或提交")
    if kind == "git_commit":
        fence = chr(96)*3
        lines = text.strip().splitlines()
        if len(lines) >= 2 and lines[0].startswith(fence) and lines[-1].strip() == fence:
            text = "\n".join(lines[1:-1])
        if not text.strip():
            raise OutputContentError(path, "文件正文为空，未写文件或提交")
        if path.endswith(".py"):
            try:
                ast.parse(text, filename=path)
            except (ValueError,SyntaxError) as err:
                raise OutputContentError(path, f"Python 语法检查未通过（第 {getattr(err,'lineno','?')} 行）") from None
        elif path.endswith(".json"):
            try:
                json.loads(text)
            except ValueError:
                raise OutputContentError(path, "不是有效 JSON") from None
    return text


class BudgetExceeded(RuntimeError):
    """单次尝试的模型费用超过上限。"""


def now_iso():
    """契约要求的 UTC ISO 8601 秒级时间。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def error(code, message, details=None):
    return {"code": code, "message": message, "retryable": RETRYABLE.get(code, False),
            "details": details or {}}


def in_scope(path, write_paths):
    """按路径段比较写入范围，避免把 app 与 app2 判成同一个前缀。"""
    target = PurePosixPath(str(path))
    parts = target.parts
    if not parts or target.is_absolute() or ".." in parts or "\\" in str(path) or ":" in str(path):
        return False
    for prefix in write_paths or []:
        prefix_parts = PurePosixPath(str(prefix)).parts
        if len(prefix_parts) <= len(parts) and parts[:len(prefix_parts)] == prefix_parts:
            return True
    return False


def build_prompt(request, task, step):
    """把任务、规格上下文、上游产物和输出定义拼成一步的提示词。"""
    if request.get("contract_slice"):
        unit = request["contract_slice"]
        relevant = request.get("spec_context") or {}
        refs = set(unit["refs"])
        facts = [item["text"][:450] for item in relevant.get("requirements",[]) if item["id"] in refs]
        facts.extend(str(item.get("choice",""))[:260] for item in relevant.get("decisions",[])[:24])
        prefix = "/api/"+unit["name"]
        data = {"module":unit["name"],"route_prefix":prefix,"scope":unit["scope"][:650],
                "requirements_and_decisions":facts,
                "occupied_routes":request.get("occupied_routes",[]),
                "correction":request.get("contract_correction","")}
        return ("只设计这个服务自己负责的 HTTP 接口，不展开其他服务。输出紧凑 JSON 且无解释："
                '{"endpoints":[{"method":"POST","path":"/api/example","handler":"create_example",'
                '"request":["title"],"response":["id"]}]}。'
                "每个服务最多 8 个接口，每种请求或响应最多 12 个英文字段名；"
                "所有路径都必须等于 route_prefix 或从 route_prefix 加斜杠开始。"
                "相同需求编号可由多个服务共同实现，但这里只做本服务范围，不负责其他模块。"
                "每个接口的 handler 在本片段内唯一；不用写示例、JSON Schema、描述或 OpenAPI 外壳。\n"
                +json.dumps(data,ensure_ascii=False,separators=(",",":")))
    spec = request.get("spec_ref") or {}
    supplied = request.get("supplied_inputs") or {}
    lines = [f'任务 {task["id"]}：{task["title"]}',
             f'阶段 {task.get("phase")}，角色 {task.get("role")}，本步 {step["stage"]}',
             f'规格 {spec.get("id")}@{spec.get("version")}',
             f'完成条件：{task.get("completion_condition")}']
    context = request.get("spec_context") or {}
    for item in context.get("requirements", []):
        lines.append(f'需求 {item.get("id")}：{item.get("text", "")}')
    for item in context.get("decisions", []):
        lines.append(f'决策 {item.get("id")}：{item.get("choice", "")}')
    for item in context.get("open_items", []):
        lines.append(f'补充 {item.get("id")}：{supplied.get(item.get("id"), "")}')
    for artifact in request.get("inputs", []):
        if artifact.get("kind") == "text":
            lines.append(f'上游 {artifact.get("task_id")} 文本：{artifact.get("content")}')
        else:
            lines.append(f'上游 {artifact.get("task_id")} 提交：{artifact.get("commit_sha")}')
    if request.get("retry_feedback"):
        lines.append("上次尝试的失败或复核反馈（本次修正）："+request["retry_feedback"])
    if request.get("handoff_package"):
        lines.append("随后并发开发的工作包：\n"+request["handoff_package"])
    output_path = (task.get("outputs") or [{}])[0].get("path") or ""
    for path, content in request.get("upstream_files", {}).items():
        if path == CONTRACT_PATH:
            content = view_for_file(content,output_path)
        lines.append(f"已批准的上游文件 {path}：\n{content}")
    for path, content in request.get("generated_files", {}).items():
        lines.append(f"本任务已生成文件 {path}：\n{content}")
    if request.get("boundary_guidance"):
        lines.append("已确认的实现边界：" + request["boundary_guidance"])
    for path, content in request.get("existing_files", {}).items():
        lines.append(f"现有文件 {path}：\n{content}")
    if output_path == "docs/technical-handoff.md" and CONTRACT_PATH in request.get("generated_files",{}):
        lines.append("输出简短交接文档，直接引用已经统一的接口契约；不要复制整份 JSON、展开请求响应示例或重新设计路由。")
    if request.get("compact_retry"):
        lines.append("上一份输出过长。本次仍需返回完整目标文件；只实现当前文件职责，省略重复注释、示例和讲解。")
    for output in task.get("outputs", []):
        lines.append(f'输出 {output.get("id")}：{output.get("path") or "文本"}')
    return "\n".join(lines)


class PromptExecutor:
    """真实执行的骨架：一次模型调用完成一个路由步骤。"""

    name = "prompt"

    def run_step(self, request, task, step, provider):
        prompt = build_prompt(request, task, step)
        kwargs = {"file_output":step["stage"]!="boundary" or bool(request.get("compact_retry"))} if isinstance(provider,OpenAICompatibleProvider) else {}
        return provider.complete(step["model"], prompt, system=SYSTEM_PROMPT,
                                 max_output_tokens=step.get("max_output_tokens"), **kwargs)


class AutoGenExecutor(PromptExecutor):
    """AutoGen 0.2 适配位。

    保留 run_step 契约，每个路由步骤开一轮 Agent 会话，用它承载
    "高能力模型定边界、低成本模型实现"的两步走。未安装 autogen 时，
    Worker 回报 EXECUTION_FAILED。
    """

    name = "autogen"

    def __init__(self, max_turns=1):
        self.max_turns = max_turns

    def run_step(self, request, task, step, provider):
        import autogen

        prompt = build_prompt(request, task, step)
        entry = {"model": step["model"]}
        if getattr(provider, "base_url", None):
            entry["base_url"] = provider.base_url
        if getattr(provider, "api_key", ""):
            entry["api_key"] = provider.api_key
        assistant = autogen.AssistantAgent(
            name="worker",
            llm_config={"config_list": [entry], "cache_seed": None, "timeout": 120})
        operator = autogen.UserProxyAgent(name="operator", human_input_mode="NEVER",
                                          code_execution_config=False)
        operator.initiate_chat(assistant, message=prompt, max_turns=self.max_turns)
        text = assistant.last_message(operator)["content"]
        return LLMResult(text, estimate_tokens(prompt), estimate_tokens(text))


class Worker:
    """执行单次尝试。

    mode 为 mock/real/auto。auto 在配置了仓库且请求带 base_commit 时走真实链路，
    否则走模拟链路，这样编排器换入真实 Worker 时不改请求形状。
    """

    def __init__(self, mode="auto", repo_root=None, worktree_root=None, router=None,
                 provider=None, executor=None):
        self.mode = mode
        self.repo_root = repo_root or os.environ.get("SE_AGENT_REPO")
        self.worktree_root = worktree_root
        self.router = router or ModelRouter(load_config())
        self.provider = provider
        self.executor = executor or PromptExecutor()
        self._providers = {}

    def execute(self, request, progress=None):
        progress = progress or (lambda *args: None)
        started_at = now_iso()
        task = request["task"]
        mode = self._mode_for(request)
        steps = self.router.plan(task, request.get("spec_context"))
        state = {"checkout_commit": None, "provider": steps[-1]["provider"],
                 "model": steps[-1]["model"], "usage": None}
        if mode == "mock":
            state.update(provider="mock", model=None)

        missing = self._missing_inputs(request)
        if missing:
            return self._result(request, "input_required", started_at, state=state,
                                error=self._missing_error(request, missing))

        progress("生成模拟产物" if mode == "mock" else "准备 Git 工作区")
        try:
            artifacts = (self._mock_artifacts(request) if mode == "mock"
                         else self._real_artifacts(request, task, state, steps, progress))
        except FileResponseError as exc:
            return self._result(request,"failed",started_at,state=state,
                                error=error(EXECUTION_FAILED,str(exc),exc.details))
        except OutputContentError as exc:
            return self._result(request, "failed", started_at, state=state,
                                error=error(OUTPUT_INVALID, str(exc), {"path":exc.path}))
        except MergeConflict as exc:
            return self._result(request, "failed", started_at, state=state,
                                error=error(MERGE_CONFLICT, "上游提交无法合并",
                                            {"paths": exc.paths}))
        except ProviderTimeout as exc:
            return self._result(request, "failed", started_at, state=state,
                                error=error(WORKER_TIMEOUT, str(exc)))
        except BudgetExceeded as exc:
            return self._result(request, "failed", started_at, state=state,
                                error=error(EXECUTION_FAILED, "超出单次尝试费用上限",
                                            {"limit_usd": self.router.max_cost_usd,
                                             "spent_usd": round(exc.args[1], 6)}))
        except (ProviderError, WorkspaceError, OSError) as exc:
            return self._result(request, "failed", started_at, state=state,
                                error=error(EXECUTION_FAILED, str(exc)))
        except ImportError as exc:
            return self._result(request, "failed", started_at, state=state,
                                error=error(EXECUTION_FAILED, f"执行器依赖缺失：{exc}"))

        problems = self._output_problems(task, artifacts)
        if problems:
            return self._result(request, "failed", started_at, state=state,
                                error=error(OUTPUT_INVALID, "候选产物不满足输出定义或写入范围",
                                            {"paths": problems}))

        return self._result(request, "succeeded", started_at, state=state, artifacts=artifacts)

    # ---- 请求检查 ----

    def _mode_for(self, request):
        if self.mode in ("mock", "real"):
            return self.mode
        return "real" if self.repo_root and request.get("base_commit") else "mock"

    @staticmethod
    def _missing_inputs(request):
        supplied = request.get("supplied_inputs") or {}
        return [item_id for item_id in request["task"].get("open_item_ids", [])
                if not str(supplied.get(item_id, "")).strip()]

    @staticmethod
    def _missing_error(request, missing):
        questions = {item.get("id"): item.get("question", "")
                     for item in (request.get("spec_context") or {}).get("open_items", [])}
        return error(MISSING_INPUT, "缺少阻塞型未决项输入",
                     {"item_ids": missing,
                      "questions": {i: questions.get(i) or f"请补充 {i} 的取值"
                                    for i in missing}})

    # ---- 模拟链路 ----

    def _mock_artifacts(self, request):
        return [self._mock_artifact(request, output)
                for output in request["task"].get("outputs", [])]

    def _mock_artifact(self, request, output):
        if output.get("kind") == "text":
            return {"output_id": output["id"], "kind": "text", "commit_sha": None,
                    "changed_paths": [], "content": self._mock_text(request)}
        return {"output_id": output["id"], "kind": "git_commit",
                "commit_sha": self._mock_sha(request, output),
                "changed_paths": [p for p in [output.get("path")] if p],
                "content": None}

    @staticmethod
    def _mock_sha(request, output):
        """模拟没有真实提交，用一个稳定的占位提交号占住形状。"""
        seed = f'{request["run_id"]}:{request["attempt_id"]}:{output["id"]}'
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _mock_text(request):
        task = request["task"]
        spec = request.get("spec_ref") or {}
        refs = "、".join(f'{a["task_id"]}@v{a["version"]}'
                         for a in request.get("inputs", [])) or "无"
        return (f'{task["title"]}\n规格 {spec.get("id")}@{spec.get("version")}\n'
                f'上游版本 {refs}\n完成条件 {task.get("completion_condition")}')

    # ---- 真实链路 ----

    def _real_artifacts(self, request, task, state, steps, progress):
        workspace = WorktreeAdapter(self.repo_root, self.worktree_root)
        workspace.prepare(request["run_id"], request["attempt_id"], request.get("base_commit"))
        state["checkout_commit"] = workspace.include(self._upstream_commits(request))

        existing = {}
        for output in task.get("outputs", []):
            path = output.get("path")
            if not path:
                continue
            if not in_scope(path, task.get("write_paths")):
                raise WorkspaceError("输出路径不在本任务写入范围内")
            target = (workspace.path / PurePosixPath(path)).resolve()
            if not target.is_relative_to(workspace.path.resolve()):
                raise WorkspaceError("输出路径超出本次工作区")
            if target.is_file():
                existing[path] = target.read_text(encoding="utf-8")[:64000]
        upstream = {}
        repository = workspace.path.resolve()
        remaining = 64000
        for artifact in request.get("inputs", []):
            for path in artifact.get("changed_paths", []):
                source = (repository / PurePosixPath(path)).resolve()
                if source.is_relative_to(repository) and source.is_file() and remaining>0 and path not in upstream:
                    with source.open(encoding="utf-8", errors="replace") as file:
                        cap = 32000 if path == CONTRACT_PATH else 12000
                        upstream[path] = file.read(min(cap,remaining))
                    remaining -= len(upstream[path])
        produced = self._run_steps(dict(request, existing_files=existing, upstream_files=upstream),
                                   task, steps, state, progress=progress)
        progress("保存产物与校验改动")
        outputs = task.get("outputs", [])
        git_outputs = [o for o in outputs if o.get("kind") == "git_commit"]
        changed = []
        if git_outputs:
            for output in git_outputs:
                target = workspace.path / PurePosixPath(output["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(produced[output["id"]], encoding="utf-8")
            state["commit_sha"] = workspace.commit_paths(
                f'{task["id"]} {task["title"]}（尝试 {request["attempt_id"]}）',
                [o["path"] for o in git_outputs])
            changed = workspace.diff_paths(state["checkout_commit"])
        return [self._real_artifact(output, produced, changed, state) for output in outputs]

    @staticmethod
    def _real_artifact(output, produced, changed, state):
        if output.get("kind") == "text":
            return {"output_id": output["id"], "kind": "text", "commit_sha": None,
                    "changed_paths": [], "content": produced.get(output["id"], "")}
        return {"output_id": output["id"], "kind": "git_commit",
                "commit_sha": state.get("commit_sha"),
                "changed_paths": list(changed), "content": None}

    @staticmethod
    def _upstream_commits(request):
        """已批准的直接上游 Git 提交，按 task_id, output_id 升序纳入。"""
        artifacts = [a for a in request.get("inputs", [])
                     if a.get("kind") == "git_commit" and a.get("status") == "approved"
                     and a.get("commit_sha")]
        artifacts.sort(key=lambda a: (a["task_id"], a["output_id"]))
        return list(dict.fromkeys(a["commit_sha"] for a in artifacts))

    def _run_steps(self, request, task, steps, state, progress=None):
        produced = {}
        spent = 0.0
        total = {"input_tokens": 0, "output_tokens": 0}
        context = dict(request, generated_files={})
        def charge(model, usage):
            nonlocal spent
            for key in total:
                total[key] += usage.get(key,0)
            state["usage"] = dict(total)
            spent += self.router.cost_usd(model,usage)
            if self.router.max_cost_usd and spent > self.router.max_cost_usd:
                raise BudgetExceeded("费用超限",spent)

        def generate(call_context, current_task, step, provider, path):
            state["model"] = step["model"]
            try:
                result = self.executor.run_step(call_context,current_task,step,provider)
            except ProviderOutputError as exc:
                if exc.usage:
                    charge(step["model"],exc.usage)
                cap = step.get("max_output_tokens")
                if not (exc.finish_reason=="length" and type(cap) is int and 0<cap<8192
                        and isinstance(provider,OpenAICompatibleProvider)
                        and provider.direct_deepseek(step["model"])):
                    raise FileResponseError(path,exc) from exc
                if progress:
                    progress("输出触顶，缩短提示并补全 "+path,int(provider.timeout))
                try:
                    result = self.executor.run_step(dict(call_context,compact_retry=True),
                        current_task,dict(step,max_output_tokens=min(8192,cap*2)),provider)
                except ProviderOutputError as again:
                    if again.usage:
                        charge(step["model"],again.usage)
                    raise FileResponseError(path,again) from again
            charge(step["model"],result.usage)
            return result.text

        def contract_for(units, current_task, step, provider):
            occupied, modules = set(), {}
            cap = min(step.get("max_output_tokens") or 2048,2048)
            for unit in units:
                name = unit["name"]
                path = f"{CONTRACT_PATH} [{name}]"
                if progress:
                    progress("生成独立接口片段 "+name, int(getattr(provider,"timeout",0) or 0))
                scoped = dict(context,contract_slice=unit,occupied_routes=sorted(occupied))
                for index in range(2):
                    generated = generate(scoped,current_task,dict(step,max_output_tokens=cap),provider,path)
                    generated = checked_content(generated,path,"git_commit")
                    try:
                        endpoints, keys = parse_endpoints(generated,occupied,prefix="/api/"+name)
                    except ValueError as exc:
                        if index:
                            raise OutputContentError(path,str(exc)) from exc
                        scoped["contract_correction"] = str(exc)
                    else:
                        modules[name] = {"source_file":unit["path"],"endpoints":endpoints}
                        occupied.update(keys)
                        break
            return json.dumps({"format":"se-agent-http-v1","modules":modules},
                              ensure_ascii=False,indent=2)+"\n"

        for step in steps:
            units = context.get("handoff_units") or []
            outputs = [None] if step["stage"]=="boundary" else task.get("outputs",[])
            if units and outputs and outputs[0] is not None:
                outputs = sorted(outputs,key=lambda item:item.get("path")!=CONTRACT_PATH)
            for output in outputs:
                current_task = task if output is None else dict(task,outputs=[output])
                provider = self._provider(step)
                path = "实现边界" if output is None else output.get("path") or output["id"]
                if progress:
                    timeout = getattr(provider,"timeout",0)
                    progress("模型核对实现边界" if output is None else "模型生成 "+path,
                             int(timeout) if isinstance(timeout,(int,float)) else 0)
                text = (contract_for(units,current_task,step,provider) if units and path==CONTRACT_PATH
                        else generate(context,current_task,step,provider,path))
                content = checked_content(text,path,output.get("kind") if output else None)
                if output is None:
                    context["boundary_guidance"] = content
                else:
                    produced[output["id"]] = content
                    context["generated_files"][path] = content[:32000 if path==CONTRACT_PATH else 12000]
        return produced

    def _provider(self, step):
        if self.provider is not None:
            return self.provider
        if step["provider"] not in self._providers:
            self._providers[step["provider"]] = build_provider(step)
        return self._providers[step["provider"]]

    # ---- 回报 ----

    @staticmethod
    def _output_problems(task, artifacts):
        """每个输出恰有一条候选产物，且改动路径落在写入范围内。"""
        problems = []
        seen = {}
        for artifact in artifacts:
            seen.setdefault(artifact["output_id"], []).append(artifact)
        write_paths = task.get("write_paths", [])
        for output in task.get("outputs", []):
            matches = seen.get(output["id"], [])
            if len(matches) != 1 or matches[0]["kind"] != output["kind"]:
                problems.append(output["id"])
                continue
            artifact = matches[0]
            if output.get("kind") == "text":
                if not isinstance(artifact["content"], str):
                    problems.append(output["id"])
            elif not artifact["commit_sha"]:
                problems.append(output["id"])
            problems.extend(path for path in artifact["changed_paths"]
                            if not in_scope(path, write_paths))
        return sorted(set(problems))

    @staticmethod
    def _result(request, status, started_at, state=None, artifacts=None, error=None):
        state = state or {}
        return {"attempt_id": request["attempt_id"],
                "status": status,
                "artifacts": artifacts or [],
                "error": error,
                "provider": state.get("provider"),
                "model": state.get("model"),
                "usage": state.get("usage"),
                "checkout_commit": state.get("checkout_commit"),
                "started_at": started_at,
                "ended_at": now_iso()}


def build_worker():
    """按环境变量组装默认 Worker。

    SE_AGENT_MODE 取 mock/real/auto，SE_AGENT_REPO 指向目标仓库，
    SE_AGENT_EXECUTOR=autogen 时改用 AutoGen 0.2 适配器。
    """
    executor = (AutoGenExecutor() if os.environ.get("SE_AGENT_EXECUTOR") == "autogen"
                else PromptExecutor())
    return Worker(mode=os.environ.get("SE_AGENT_MODE", "auto"),
                  repo_root=os.environ.get("SE_AGENT_REPO"),
                  router=ModelRouter(load_config()),
                  executor=executor)


def execute_task(request, worker=None):
    """v2 入口：执行一次尝试并回报 WorkerResult。"""
    return (worker or build_worker()).execute(request)

"""Worker adapter: hand one WorkerRequest to an executor and record the result.

The orchestration side owns attempts, leases and release; the executor only
answers one request at a time. ``run_once`` is the loop the runner plugs into:
swap ``simulated`` for 胡子昂's real executor and nothing above changes.
"""
from .contracts import InvalidState, InvalidReference


def simulated(request):
    """Stand-in executor: one artifact per declared output, no real work."""
    artifacts = []
    for output in request["task"]["outputs"]:
        if output["kind"] == "git_commit":
            artifacts.append({"output_id": output["id"], "kind": "git_commit",
                              "commit_sha": "0" * 40,
                              "changed_paths": request["task"]["write_paths"],
                              "content": None})
        else:
            artifacts.append({"output_id": output["id"], "kind": "text",
                              "commit_sha": None, "changed_paths": [],
                              "content": f'{request["task"]["title"]} 的交付说明'})
    return {"attempt_id": request["attempt_id"], "status": "succeeded",
            "artifacts": artifacts, "error": None, "provider": "mock",
            "model": None, "usage": None, "checkout_commit": None,
            "started_at": None, "ended_at": None}


def _failure(request, message, code="EXECUTION_FAILED"):
    return {"attempt_id": request["attempt_id"], "status": "failed",
            "artifacts": [], "error": {"code": code, "message": message,
                                       "retryable": True, "details": {}},
            "provider": "unknown", "model": None, "usage": None,
            "checkout_commit": None, "started_at": None, "ended_at": None}


def run_once(store, execute, run_id, worker_id="worker-1", lease_seconds=300,
             limit=None):
    """Claim one batch, execute each request, submit each result."""
    if not run_id:
        raise InvalidReference("需要指定运行编号", field="run_id")
    handed = store.claim(run_id, worker_id, lease_seconds=lease_seconds, limit=limit)
    results = []
    for request in handed:
        try:
            result = execute(request)
        except Exception as exc:                      # a crash is a reportable result
            result = _failure(request, str(exc))
        if not isinstance(result, dict):
            result = _failure(request, "执行器未返回结果对象")
        result = dict(result)
        result["attempt_id"] = request["attempt_id"]
        store.submit(run_id, result, worker_id)
        results.append(result)
    return results


def drive(store, execute, run_id, worker_id="worker-1", lease_seconds=300,
          rounds=100):
    """Dispatch until nothing is left to hand out.

    Automatic runs claim whatever is ready. Manual runs first fix a batch with
    ``step``; an empty or exhausted queue simply ends this round.
    """
    processed = []
    for _ in range(rounds):
        store.expire_leases(run_id)
        snapshot = store.snapshot(run_id)
        if snapshot["state"] == "completed":
            break
        if snapshot["mode"] == "manual":
            try:
                store.step(run_id)
            except InvalidState:
                pass                     # queue still open, or nothing to dispatch
        batch = run_once(store, execute, run_id, worker_id, lease_seconds)
        if not batch:
            break
        processed.extend(batch)
    return processed
