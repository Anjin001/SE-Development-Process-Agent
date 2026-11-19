"""Integrated QLM workbench. Run python -m se_agent.workbench --port 8008."""
from .limits import MAX_JSON_BODY_BYTES, public_limits

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from threading import RLock
from urllib.parse import parse_qs, urlsplit

from .api_v2 import Router
from .contracts import DomainError, InvalidReference, InvalidState, NotFound
from .gitcheck import CommitVerifier
from .store import Store
from .synthesis_preview import PreviewError
from .workbench_preview import WorkbenchHandler, PROTOTYPES, PAGES
from .workspace import ProjectReviews, Workspace
from .workspace_experience import Progress, help_answer
from .steward_pipeline import DEFAULT_RACE_PARALLEL, StewardPipeline
from .agent_client import MODEL_TIMEOUT_SECONDS, AgentError, ChatAgent, configured_agent
from .env_file import load_env
from .model_router import ModelRouter
from .providers import OpenAICompatibleProvider
from .worker import Worker
from http.server import ThreadingHTTPServer
import socket

class LocalWorkbenchServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


PROJECT = re.compile(r"^/workspace/projects/([^/]+)(?:/(.*))?$")


class Handler(WorkbenchHandler):
    def do_PATCH(self):
        self.route()

    def route(self):
        path = urlsplit(self.path).path
        if path in ("/workspace-link.js","/steward-link.js") and self.command == "GET":
            self.respond(200, (PROTOTYPES / path[1:]).read_bytes(), "text/javascript; charset=utf-8")
            return
        if path == "/workspace/steward" or path.startswith("/workspace/steward/"):
            self.steward_route(path)
            return
        if path.startswith("/workspace/progress/") and self.command == "GET":
            self.respond(200, {"steps": self.server.progress.read(path.rsplit("/", 1)[1])})
            return
        if path == "/workspace/help" and self.command == "POST":
            try:
                self.require_local_json()
                body = self.body()
                context = {}
                project_id = body.get("project_id")
                if isinstance(project_id, str) and project_id:
                    with self.server.review_lock, self.server.workspace.database._lock:
                        project = self.server.workspace.project(project_id)
                    run = project["run"]
                    if run:
                        tasks = list(run["tasks"].values())
                        context = dict(run_id=run["id"], state=run["state"], dispatch=run["dispatch"],
                                       executing=sum(task["state"] == "running" for task in tasks),
                                       waiting=sum(task["state"] == "input_required" for task in tasks),
                                       done=sum(task["state"] == "done" for task in tasks))
                    else:
                        context["state"] = "not_started"
                self.respond(200, help_answer(body.get("question"), self.server.agent, context))
            except (DomainError, AgentError, ValueError, TypeError, PermissionError) as exc:
                self.problem(exc)
            return
        if path.startswith("/workspace/") or path.startswith("/api/v2/"):
            with self.server.review_lock:
                self.workspace_route(path)
            return
        if path.startswith("/preview/plans"):
            with self.server.review_lock:
                try:
                    body = self.body()
                    review = self.server.store.snapshot(self.server.store.get(body.get("review_id")))
                    if path == "/preview/plans/edit":
                        result = self.server.workspace.plans.save(
                            review, body.get("expected_version"), body.get("tasks"), body.get("max_parallel"))
                    elif path == "/preview/plans":
                        result = self.server.workspace.plans.generate(review, body.get("version"))
                    else:
                        raise NotFound("接口不存在")
                    self.respond(200, result)
                except (PreviewError, DomainError, ValueError, TypeError, KeyError) as exc:
                    self.problem(exc)
            return
        super().route()

    def body(self):
        if self.command == "GET":
            return {}
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= MAX_JSON_BODY_BYTES:
            raise ValueError("请求体须为 1 MB 以内的 JSON")
        data = json.loads(self.rfile.read(size), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(data, dict):
            raise ValueError("请求体必须为 JSON 对象")
        return data

    def problem(self, exc):
        if isinstance(exc, DomainError):
            self.respond(exc.status, exc.as_body())
        elif isinstance(exc, PreviewError):
            self.respond(exc.status, {"error": dict(code=exc.code, message=str(exc), details=exc.details)})
        elif isinstance(exc, AgentError):
            self.respond(502, {"error": dict(code="MODEL_ERROR", message=str(exc), details={})})
        elif isinstance(exc, PermissionError):
            self.respond(403, {"error": dict(code="FORBIDDEN", message=str(exc), details={})})
        elif isinstance(exc, OSError):
            self.respond(400, {"error": dict(code="OUTPUT_ERROR",
                                            message="无法写入指定文件夹，请检查位置或访问权限。", details={})})
        else:
            self.respond(400, {"error": dict(code="INVALID_REQUEST", message=str(exc), details={})})

    def steward_route(self, path):
        pipeline = self.server.steward
        try:
            if self.command == "POST":
                self.require_local_json()
                payload = self.body()
                if path == "/workspace/steward":
                    self.respond(202, pipeline.start(payload.get("markdown"), payload.get("settings"),
                                                     payload.get("project_id")))
                    return
                match = re.fullmatch(r"/workspace/steward/([a-f0-9]{32})/(chat|gates)", path)
                if match:
                    key, action = match.groups()
                    if action == "chat":
                        self.respond(200, pipeline.chat(key, payload.get("message"), payload.get("command"), payload.get("settings")))
                    else:
                        self.respond(200, pipeline.set_gates(key, payload.get("gates")))
                    return
            if self.command == "GET":
                if path == "/workspace/steward":
                    self.respond(200, {"items":pipeline.list(), "race_parallel":DEFAULT_RACE_PARALLEL, "team_plan":True})
                    return
                match = re.fullmatch(r"/workspace/steward/([a-f0-9]{32})", path)
                if match:
                    self.respond(200, pipeline.public(match[1]))
                    return
            raise NotFound("主理入口不存在。")
        except (DomainError, PreviewError, AgentError, TypeError, ValueError, OSError) as exc:
            self.problem(exc)

    def workspace_route(self, path):
        workspace = self.server.workspace
        try:
            body = self.body()
            query = parse_qs(urlsplit(self.path).query)
            if path == "/workspace/model-settings":
                self.respond(200, self.model_settings(body))
                return
            if path.startswith("/api/v2/"):
                with workspace.database.tx():
                    status, result = self.server.router.handle(
                        self.command, path, body, dict(self.headers),
                        {key: values[0] for key, values in query.items()})
                self.respond(status, result)
                return
            if path in ("/workspace/docs/runtime", "/workspace/docs/contract") and self.command == "GET":
                filename = "整合运行说明.md" if path.endswith("runtime") else "接口契约-v2.md"
                self.respond(200, {"markdown": (PROTOTYPES.parent / "docs" / filename).read_text(encoding="utf-8")})
                return
            if path == "/workspace/config" and self.command == "GET":
                self.respond(200, dict(self.model_details(), projects=workspace.projects(), limits=public_limits()))
                return
            match = PROJECT.fullmatch(path)
            if not match:
                raise NotFound("接口不存在")
            project_id, suffix = match.groups()
            workspace.reviews.get(project_id)
            if self.command == "GET":
                with workspace.database._lock:
                    if suffix is None:
                        result = workspace.project(project_id)
                    elif suffix == "memory":
                        terms = query.get("terms", [""])[0].replace("，", ",").split(",")
                        result = dict(items=workspace.experiences(
                            project_id, [term.strip() for term in terms if term.strip()],
                            query.get("domain", [""])[0]))
                    elif suffix == "memory/records":
                        result = dict(items=workspace.database.memory.list_records(
                            project_id, workspace.actor, "retrospective"))
                    elif suffix and re.fullmatch(r"runs/[^/]+/questions", suffix):
                        result = dict(items=workspace.steward_questions(project_id, suffix.split("/")[1]))
                    elif suffix and suffix.startswith("runs/") and "/" not in suffix[5:]:
                        result = workspace.run(project_id, suffix[5:])
                    else:
                        raise NotFound("接口不存在")
                self.respond(200, result)
                return
            if self.command != "POST":
                raise InvalidReference("方法不被支持")
            headers = {key.lower(): value for key, value in self.headers.items()}

            def write():
                if suffix in ("target", "export"):
                    self.require_local_json()
                    if suffix == "target":
                        return 200, workspace.configure_target(project_id, body.get("path"), body.get("real") is True)
                    return 200, workspace.delivery.export(workspace.project(project_id))
                if suffix == "runs":
                    review = workspace.reviews.snapshot(workspace.reviews.get(project_id))
                    spec = review["specification"]
                    plan = workspace.plans.latest(project_id)
                    if not spec or spec["status"] != "confirmed" or not plan or plan["plan"]["spec_ref"] != {
                        "id": spec["id"], "version": spec["version"],
                    }:
                        raise InvalidState("请先交接当前确认版规格并保存任务计划")
                    if body.get("plan_ref") != dict(id=plan["plan"]["id"], version=plan["plan"]["version"]):
                        raise InvalidState("请刷新最新计划后启动")
                    if body.get("mode", "manual") not in ("manual", "auto"):
                        raise InvalidReference("运行模式应为 manual 或 auto")
                    if workspace.delivery.target(project_id)["repo"] and not plan["plan"]["base_commit"]:
                        raise InvalidState("请先为当前计划绑定 Git 仓库。")
                    result = workspace.database.create_run(
                        project_id, plan["plan"]["id"], plan["plan"]["version"], body.get("mode", "manual"))
                    if body.get("steward") is True:
                        workspace.action(project_id, result["id"], "steward-defaults",
                                         {"expected_revision": result["revision"]})
                    return 201, workspace.run(project_id, result["id"])
                if suffix and re.fullmatch(r"runs/[^/]+/actions/[a-z-]+", suffix):
                    _, run_id, _, action = suffix.split("/")
                    return 200, workspace.action(project_id, run_id, action, body)
                if suffix == "memory":
                    return 201, workspace.record_experience(project_id, body)
                if suffix == "memory/baseline":
                    return 200, workspace.refresh_baseline(project_id)
                if suffix == "memory/publish":
                    ref = workspace.publish_experience(
                        project_id, body["ref"], body["expected_version"], body.get("note", ""))
                    return 200, ref
                raise NotFound("接口不存在")

            with workspace.database.tx():
                status, result = self.server.router._idempotent(project_id, "POST", path, body, headers, write)
            self.respond(status, result)
        except (DomainError, PreviewError, AgentError, ValueError, TypeError, KeyError, OSError) as exc:
            self.problem(exc)

    def model_details(self):
        worker = self.server.workspace.dispatch.worker
        agent = self.server.agent
        tiers = worker.router.config["tiers"]
        real_available = bool(worker.repo_root) and worker.mode != "mock"
        return dict(mode="real" if real_available else "mock", real_available=real_available,
                    router=worker.router.describe(), discussion_mode="model" if agent else "mock",
                    base_url=agent.base_url if agent else os.environ.get("SE_AGENT_SETUP_BASE_URL", ""),
                    discussion_model=agent.model if agent else os.environ.get("SE_AGENT_SETUP_DISCUSSION_MODEL", ""),
                    key_set=bool(agent and agent.api_key),
                    low_cost_model=(tiers["low_cost"]["model"] if agent else
                                    os.environ.get("SE_AGENT_SETUP_LOW_MODEL", tiers["low_cost"]["model"])),
                    high_capability_model=(tiers["high_capability"]["model"] if agent else
                                          os.environ.get("SE_AGENT_SETUP_HIGH_MODEL", tiers["high_capability"]["model"])))

    def require_local_json(self):
        port = self.server.server_port
        trusted_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        if (self.command != "POST" or self.headers.get("Origin") not in trusted_origins
                or self.headers.get_content_type() != "application/json"):
            raise PermissionError("请从本地工作台页面执行此操作。")

    def model_settings(self, body):
        if self.command == "GET":
            return self.model_details()
        self.require_local_json()
        action = body.get("action")
        if action == "restore":
            self.server.agent, self.server.workspace.dispatch.worker = self.server.startup_models
            self.server.store.agent = {"agent": self.server.agent} if self.server.agent else {}
            return self.model_details()
        if action not in ("test", "save"):
            raise InvalidReference("未知模型设置操作。")

        def field(name, limit, required=True):
            value = body.get(name, "")
            if not isinstance(value, str):
                raise InvalidReference("设置项应为文本。", field=name)
            value = value.strip()
            if (required and not value) or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise InvalidReference("设置项为空或格式有误。", field=name)
            return value

        base_url = field("base_url", 500).rstrip("/")
        url = urlsplit(base_url)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
                or url.query or url.fragment or any(char.isspace() or char == "\\" for char in base_url)
                or url.path.endswith("/chat/completions")):
            raise InvalidReference("请输入 API 基础地址，例如 https://api.openai.com/v1。", field="base_url")
        url.port  # Validate an explicitly supplied port.
        discussion = field("discussion_model", 160)
        key = field("api_key", 4096, required=False)
        previous = self.server.agent
        if not key and previous and previous.base_url == base_url:
            key = previous.api_key or ""
        if key and not key.isascii():
            raise InvalidReference("API 密钥格式有误。", field="api_key")

        agent = ChatAgent(base_url, discussion, key)
        if action == "test":
            agent("challenge", {"test": "连接测试。请返回一个 question 字符串。"})
            return {"ok": True}

        low_cost = field("low_cost_model", 160)
        high_capability = field("high_capability_model", 160)
        old_worker = self.server.workspace.dispatch.worker
        config = deepcopy(old_worker.router.config)
        for tier, model in (("low_cost", low_cost), ("high_capability", high_capability)):
            config["tiers"][tier].update(model=model, base_url=base_url, provider=OpenAICompatibleProvider.name)
        provider = OpenAICompatibleProvider(base_url, api_key=key)
        self.server.workspace.dispatch.worker = Worker(
            mode=old_worker.mode, repo_root=old_worker.repo_root, worktree_root=old_worker.worktree_root,
            router=ModelRouter(config), provider=provider, executor=old_worker.executor)
        self.server.agent = agent
        self.server.store.agent = {"agent": agent}
        return self.model_details()


def make_server(port=8008, db="data/workbench.sqlite3", worker=None):
    if db != ":memory:":
        Path(db).resolve().parent.mkdir(parents=True, exist_ok=True)
    repo = os.environ.get("SE_AGENT_REPO")
    database = Store(db, verifier=CommitVerifier(repo) if repo else None)
    try:
        server = LocalWorkbenchServer(("127.0.0.1", port), Handler)
    except OSError:
        database.close()
        raise
    server.agent = configured_agent()
    server.store = ProjectReviews(database, server.agent)
    server.workspace = Workspace(database, server.store, worker)
    server.startup_models = (server.agent, server.workspace.dispatch.worker)
    server.router = Router(database, actor_id=server.workspace.actor)
    server.review_lock = RLock()
    server.progress = Progress()
    server.steward = StewardPipeline(server.workspace, lambda: server.agent, server.review_lock)
    server.pages = {path: (PROTOTYPES / filename).read_bytes() for path, filename in PAGES.items()}
    page = server.pages["/"].decode("utf-8")
    server.pages["/"] = page.replace(
        '<script src="/planner-link.js"></script>',
        '<script src="/steward-link.js"></script>\n  <script src="/workspace-link.js"></script>\n  <script src="/planner-link.js"></script>').encode("utf-8")
    return server


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--db", default="data/workbench.sqlite3")
    args = parser.parse_args()
    try:
        server = make_server(args.port, args.db)
    except OSError as exc:
        if exc.errno in (98, 10013, 10048):
            parser.exit(1, f"无法独占端口 {args.port}。请关闭占用该端口的旧工作台后再启动。\n")
        raise
    print(f"SE-Agent 工作台 http://127.0.0.1:{server.server_port}", flush=True)
    print(f"项目目录：{Path(__file__).resolve().parent.parent}", flush=True)
    print(f"赛马与评审单次模型请求超时：{MODEL_TIMEOUT_SECONDS} 秒", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.workspace.dispatch.close()
        server.server_close()
        server.workspace.database.close()


if __name__ == "__main__":
    main()
