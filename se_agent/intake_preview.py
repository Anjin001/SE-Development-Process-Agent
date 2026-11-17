"""Local preview server for intake and configurable race groups."""
import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path

from .agent_client import AgentError, configured_agent
from .intake import EXTRA_GROUP_PROMPT, build_snapshot, default_group_prompts, run_group
from .server import Handler

PAGE = Path(__file__).resolve().parent.parent / "prototypes" / "intake-race.html"


class PreviewServer(ThreadingHTTPServer):
    def __init__(self, port):
        self.page = PAGE.read_bytes()
        self.agent = configured_agent()
        super().__init__(("127.0.0.1", port), PreviewHandler)


class PreviewHandler(Handler):
    def route(self):
        if self.command == "GET" and self.path == "/":
            self.respond(200, self.server.page, "text/html; charset=utf-8")
            return
        if self.command == "GET" and self.path == "/preview/config":
            self.send_json(200, {"prompts": default_group_prompts(), "extra_prompt": EXTRA_GROUP_PROMPT,
                                 "mode": "model" if self.server.agent else "mock"})
            return
        if self.command != "POST" or self.path not in ("/preview/snapshot", "/preview/group"):
            self.send_json(404, {"error": "接口不存在"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 256_000:
                raise ValueError("请求体长度无效")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
            snapshot = build_snapshot(body.get("markdown"), "REQSET-DEMO", body.get("version"))
            if self.path == "/preview/snapshot":
                self.send_json(200, snapshot)
                return
            if body.get("simulate_failure") is True:
                self.send_json(503, {"error": "模拟生成中断，可单独重试这一组。"})
                return
            kwargs = {"agent": self.server.agent} if self.server.agent else {}
            self.send_json(200, run_group(snapshot, body.get("group_id"), prompt=body.get("prompt"), **kwargs))
        except AgentError as exc:
            self.send_json(502, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})


def make_preview_server(port=8001):
    return PreviewServer(port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    with make_preview_server(args.port) as server:
        print(f"赛马工作台 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
