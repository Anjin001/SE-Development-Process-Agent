"""Stdlib HTTP adapter for the /api/v2 router.

Temporary transport until the FastAPI app lands: same paths, same bodies, so
a later mount changes nothing above ``Router``. Run with
``python -m se_agent.api_v2_http --port 8001``.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlparse

from .api_v2 import Router
from .store import Store

MAX_BODY = 256_000


class Handler(BaseHTTPRequestHandler):
    router = None

    def _handle(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = {}
        if length:
            if length > MAX_BODY:
                self._send(400, {"error": {"code": "INVALID_ARGUMENT",
                                           "message": "请求体过大", "details": {}}})
                return
            body = json.loads(self.rfile.read(length) or b"{}")
        status, payload = self.router.handle(
            self.command, parsed.path, body, dict(self.headers),
            dict(parse_qsl(parsed.query)))
        self._send(status, payload)

    def _send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = _handle

    def log_message(self, *args):
        pass


def make_server(store, port=8001, actor_id="tester"):
    handler = type("BoundHandler", (Handler,), {"router": Router(store, actor_id)})
    return HTTPServer(("127.0.0.1", port), handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db", default="se_agent.db")
    args = parser.parse_args()
    store = Store(args.db)
    with make_server(store, args.port) as server:
        print(f"v2 API http://127.0.0.1:{server.server_port}/api/v2", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()