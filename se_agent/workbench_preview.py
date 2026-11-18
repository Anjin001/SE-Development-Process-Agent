"""Combined local intake, review, and task planner. Run python -m se_agent.workbench_preview."""
import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from .agent_client import configured_agent
from .intake_preview import PreviewHandler as IntakeHandler
from .planner_preview import PlanPreviewStore
from .synthesis_preview import PreviewError, PreviewHandler as ReviewHandler, PreviewStore

PROTOTYPES = Path(__file__).resolve().parent.parent / "prototypes"
PAGES = {"/": "se-development-process-agent-ui.html", "/legacy-preview": "workbench.html",
         "/intake": "intake-race.html", "/review": "review-synthesis.html",
         "/planner-link.js": "planner-link.js",
         "/vendor/tailwindcss-3.4.17.js": "vendor/tailwindcss-3.4.17.js"}


class WorkbenchHandler(ReviewHandler):
    def send_json(self, status, data):
        self.respond(status, data)

    def route(self):
        path = self.path.split("?", 1)[0]
        if self.command == "GET" and path in PAGES:
            kind = "text/javascript" if path.endswith(".js") else "text/html"
            self.respond(200, self.server.pages[path], kind + "; charset=utf-8")
        elif path in {"/preview/config", "/preview/snapshot", "/preview/group"}:
            IntakeHandler.route(self)
        elif self.command == "POST" and path in {"/preview/plans", "/preview/plans/edit"}:
            with self.server.review_lock:
                self.plan_route(path)
        else:
            with self.server.review_lock:
                super().route()

    def plan_route(self, path):
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 1_000_000:
                raise ValueError("请求体须为 1 MB 以内的 JSON")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须为 JSON 对象")
            review = self.server.store.snapshot(self.server.store.get(payload.get("review_id")))
            if path.endswith("/edit"):
                data = self.server.planner.save(review, payload.get("expected_version"), payload.get("tasks"), payload.get("max_parallel"))
                self.respond(200, data)
            else:
                data = self.server.planner.generate(review, payload.get("version"))
                self.respond(201, data)
        except PreviewError as error:
            self.respond(error.status, {"error": {"code": error.code, "message": str(error), "details": error.details}})
        except (ValueError, TypeError) as error:
            self.respond(400, {"error": {"code": "INVALID_REQUEST", "message": str(error), "details": {}}})


def make_server(port=8003):
    pages = {path: (PROTOTYPES / filename).read_bytes() for path, filename in PAGES.items()}
    server = ThreadingHTTPServer(("127.0.0.1", port), WorkbenchHandler)
    server.pages = pages
    server.agent = configured_agent()
    server.store = PreviewStore(server.agent)
    server.review_lock = Lock()
    server.planner = PlanPreviewStore()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    with make_server(args.port) as server:
        print(f"赛马、评审与任务分解工作台 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
