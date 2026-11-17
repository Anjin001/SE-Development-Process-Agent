"""Combined local intake and review demo. Run python -m se_agent.workbench_preview."""
import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from .intake_preview import PreviewHandler as IntakeHandler
from .synthesis_preview import PreviewHandler as ReviewHandler, PreviewStore

PROTOTYPES = Path(__file__).resolve().parent.parent / "prototypes"
PAGES = {"/": "workbench.html", "/intake": "intake-race.html", "/review": "review-synthesis.html"}


class WorkbenchHandler(ReviewHandler):
    def send_json(self, status, data):
        self.respond(status, data)

    def route(self):
        path = self.path.split("?", 1)[0]
        if self.command == "GET" and path in PAGES:
            self.respond(200, self.server.pages[path], "text/html; charset=utf-8")
        elif path in {"/preview/config", "/preview/snapshot", "/preview/group"}:
            IntakeHandler.route(self)
        else:
            with self.server.review_lock:
                super().route()


def make_server(port=8003):
    pages = {path: (PROTOTYPES / filename).read_bytes() for path, filename in PAGES.items()}
    server = ThreadingHTTPServer(("127.0.0.1", port), WorkbenchHandler)
    server.pages = pages
    server.store = PreviewStore()
    server.review_lock = Lock()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    with make_server(args.port) as server:
        print(f"赛马与评审工作台 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
