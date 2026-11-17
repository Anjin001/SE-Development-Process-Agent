"""Local preview server dedicated to intake and the two-group race."""
import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path

from .intake import build_snapshot, run_group
from .server import Handler

PAGE = Path(__file__).resolve().parent.parent / "prototypes" / "intake-race.html"


class PreviewHandler(Handler):
    def route(self):
        if self.command == "GET" and self.path == "/":
            self.respond(200, PAGE.read_bytes(), "text/html; charset=utf-8")
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
            self.send_json(200, run_group(snapshot, body.get("group_id")))
        except (ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})


def make_preview_server(port=8001):
    return ThreadingHTTPServer(("127.0.0.1", port), PreviewHandler)


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
