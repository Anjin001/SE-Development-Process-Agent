"""Local HTTP composition root. Run with python -m se_agent.server."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from . import intake, orchestrator, planner, runner, synthesis
from .contracts import Conflict

WEB = Path(__file__).resolve().parent.parent / "web"
PROJECTS = {}


def create(markdown):
    requirement = intake.intake(markdown)
    drafts = intake.race(requirement)
    return {"id": uuid4().hex, "requirement": requirement, "drafts": drafts,
            "scores": synthesis.assess(drafts), "spec": None, "plan": None}


def change(project, action, payload):
    run = project.get("_run")
    if action == "confirm" and project["spec"] is None:
        spec = synthesis.confirm(project["requirement"], project["drafts"], payload.get("decision"))
        plan = planner.decompose(spec)
        orchestrator.validate(plan)
        project.update(spec=spec, plan=plan)
    elif action == "configure" and project["plan"] and run is None:
        plan = project["plan"]
        if payload.get("version") != plan["version"]:
            raise Conflict("计划已变化，请刷新")
        updated = {**plan, "version": plan["version"] + 1, "max_parallel": payload.get("max_parallel")}
        orchestrator.validate(updated)
        project["plan"] = updated
    elif action == "start" and project["plan"] and run is None:
        if payload.get("version") != project["plan"]["version"]:
            raise Conflict("计划已变化，请刷新")
        project["_run"] = orchestrator.Run(project["plan"])
    elif run and action == "step":
        run.step(runner.execute)
    elif run and action in {"pause", "resume"}:
        run.set_paused(action == "pause")
    elif run and action == "retry":
        run.retry(payload.get("task_id"))
    elif run and action == "review":
        run.review(payload.get("task_id"), payload.get("version"), payload.get("decision"),
                   payload.get("content"), payload.get("note", ""))
    else:
        raise Conflict("当前阶段无法执行该操作")


def snapshot(project):
    return {**{k: v for k, v in project.items() if k != "_run"},
            "run": project["_run"].snapshot() if "_run" in project else None}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, data):
        self.respond(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    def route(self):
        parts = self.path.strip("/").split("/")
        try:
            if self.command == "GET" and self.path == "/":
                self.respond(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            if parts[:2] != ["api", "projects"]:
                self.send_json(404, {"error": "未知接口"})
                return
            if self.command == "POST":
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 256_000:
                    raise ValueError("请求体长度无效")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("请求体必须为 JSON 对象")
                if len(parts) == 2:
                    project = create(payload.get("markdown"))
                    PROJECTS[project["id"]] = project
                    self.send_json(201, snapshot(project))
                    return
            if len(parts) >= 3 and parts[2] in PROJECTS:
                project = PROJECTS[parts[2]]
                if self.command == "GET" and len(parts) == 3:
                    self.send_json(200, snapshot(project))
                    return
                if self.command == "POST" and len(parts) == 5 and parts[3] == "actions":
                    change(project, parts[4], payload)
                    self.send_json(200, snapshot(project))
                    return
            self.send_json(404, {"error": "项目或接口不存在"})
        except Conflict as exc:
            self.send_json(409, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})


def make_server(port=8000):
    return HTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with make_server(args.port) as server:
        print(f"工作台 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
