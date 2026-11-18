import json
from io import BytesIO
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from se_agent.agent_client import AgentError, ChatAgent
from se_agent.workbench import make_server
from se_agent.worker import Worker


class ModelSettingsTest(unittest.TestCase):
    def test_model_settings_wire_discussion_and_worker_without_returning_credentials(self):
        calls = []

        class MockProvider(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, self.headers.get("Authorization"), data["model"]))
                reply = {"choices": [{"message": {"content": '{"question":"ok"}'}}]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(reply).encode())

        with HTTPServer(("127.0.0.1", 0), MockProvider) as provider, make_server(
                0, ":memory:", worker=Worker(mode="mock")) as app:
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (provider, app)]
            for thread in threads:
                thread.start()
            base = f"http://127.0.0.1:{app.server_port}"
            endpoint = f"http://127.0.0.1:{provider.server_port}/v1"
            secret = "test-key-local-only"
            settings = dict(base_url=endpoint, discussion_model="discussion", api_key=secret,
                            low_cost_model="low", high_capability_model="high")

            def request(path, data=None, origin=base):
                payload = json.dumps(data).encode() if data is not None else None
                req = Request(base + path, data=payload,
                              headers={"Origin": origin, "Content-Type": "application/json"})
                with urlopen(req, timeout=5) as response:
                    data = response.read().decode()
                self.assertNotIn(secret, data)
                return json.loads(data)

            try:
                path = "/workspace/model-settings"
                startup = request(path)
                with self.assertRaises(HTTPError) as rejected:
                    request(path, dict(settings, action="save"), origin="https://other.example")
                self.assertEqual(rejected.exception.code, 403)
                rejected.exception.close()
                self.assertTrue(request(path, dict(settings, action="test"))["ok"])
                self.assertEqual(request(path), startup)
                saved = request(path, dict(settings, action="save"))
                self.assertEqual(saved["discussion_mode"], "model")
                self.assertTrue(saved["key_set"])
                self.assertEqual(request("/preview/config")["mode"], "model")
                self.assertIs(app.store.agent["agent"], app.agent)
                worker = app.workspace.dispatch.worker
                step = worker.router.plan({"role": "develop"})[0]
                worker._provider(step).complete(step["model"], "connection check")
                self.assertEqual(calls, [("/v1/chat/completions", f"Bearer {secret}", "discussion"),
                                         ("/v1/chat/completions", f"Bearer {secret}", "low")])
                kept = request(path, dict(settings, action="save", api_key=""))
                self.assertTrue(kept["key_set"])
                self.assertEqual(request(path, {"action": "restore"}), startup)
            finally:
                for server in (app, provider):
                    server.shutdown()
                for thread in threads:
                    thread.join(timeout=2)
                app.workspace.dispatch.close()
                app.workspace.database.close()


    def test_model_failures_show_stage_and_safe_reason(self):
        agent = ChatAgent("http://example.invalid/v1", "demo", "private-key")
        rejection = HTTPError(agent.endpoint, 429, "private-reason", {},
                              BytesIO(b"private-response"))
        with patch("se_agent.agent_client.urlopen", side_effect=rejection):
            with self.assertRaisesRegex(AgentError, r"生成草案.*429.*限流") as caught:
                agent("draft", {})
        self.assertNotIn("private-", str(caught.exception))

        with patch("se_agent.agent_client.urlopen", side_effect=TimeoutError("private-url")):
            with self.assertRaisesRegex(AgentError, r"独立构思.*超时"):
                agent("idea", {})
        malformed = {"choices": [{"message": {"content": "private-non-json"}}]}
        with patch("se_agent.agent_client.urlopen",
                   return_value=BytesIO(json.dumps(malformed).encode())):
            with self.assertRaisesRegex(AgentError, r"生成草案.*JSON 结构") as caught:
                agent("draft", {})
        self.assertNotIn("private-", str(caught.exception))
