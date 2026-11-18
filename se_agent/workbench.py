"""Integrated QLM workbench. Run python -m se_agent.workbench --port 8008."""
import argparse
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
from .agent_client import configured_agent
from http.server import ThreadingHTTPServer

PROJECT = re.compile(r"^/workspace/projects/([^/]+)(?:/(.*))?$")


class Handler(WorkbenchHandler):
    def do_PATCH(self):
        self.route()

    def route(self):
        path = urlsplit(self.path).path
        if path == "/workspace-link.js" and self.command == "GET":
            self.respond(200, (PROTOTYPES / "workspace-link.js").read_bytes(), "text/javascript; charset=utf-8")
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
        if not 0 < size <= 1_000_000:
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
        elif isinstance(exc, PermissionError):
            self.respond(403, {"error": dict(code="FORBIDDEN", message=str(exc), details={})})
        else:
            self.respond(400, {"error": dict(code="INVALID_REQUEST", message=str(exc), details={})})

    def workspace_route(self, path):
        workspace = self.server.workspace
        try:
            body = self.body()
            query = parse_qs(urlsplit(self.path).query)
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
                worker = workspace.dispatch.worker
                effective_mode = worker.mode if worker.mode != "auto" else ("real" if worker.repo_root else "mock")
                self.respond(200, dict(mode=effective_mode, real_available=bool(worker.repo_root),
                                       router=worker.router.describe(), projects=workspace.projects()))
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
                    result = workspace.database.create_run(
                        project_id, plan["plan"]["id"], plan["plan"]["version"], body.get("mode", "manual"))
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
        except (DomainError, PreviewError, ValueError, TypeError, KeyError, PermissionError) as exc:
            self.problem(exc)


def make_server(port=8008, db="data/workbench.sqlite3", worker=None):
    if db != ":memory:":
        Path(db).resolve().parent.mkdir(parents=True, exist_ok=True)
    repo = os.environ.get("SE_AGENT_REPO")
    database = Store(db, verifier=CommitVerifier(repo) if repo else None)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.agent = configured_agent()
    server.store = ProjectReviews(database, server.agent)
    server.workspace = Workspace(database, server.store, worker)
    server.router = Router(database, actor_id=server.workspace.actor)
    server.review_lock = RLock()
    server.pages = {path: (PROTOTYPES / filename).read_bytes() for path, filename in PAGES.items()}
    page = server.pages["/"].decode("utf-8")
    server.pages["/"] = page.replace(
        '<script src="/planner-link.js"></script>',
        '<script src="/workspace-link.js"></script>\n  <script src="/planner-link.js"></script>').encode("utf-8")
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--db", default="data/workbench.sqlite3")
    args = parser.parse_args()
    server = make_server(args.port, args.db)
    print(f"SE-Agent 工作台 http://127.0.0.1:{server.server_port}", flush=True)
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
