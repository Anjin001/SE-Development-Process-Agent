import json
import threading
import unittest
from urllib.request import Request, urlopen

from se_agent.server import make_server


class HttpTest(unittest.TestCase):
    def test_browser_entry_and_project_round_trip(self):
        with make_server(0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            root = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(root, timeout=5) as response:
                    self.assertIn("SE 开发工作台", response.read().decode())
                request = Request(root + "/api/projects", data=json.dumps({"markdown": "设备预约"}).encode(),
                                  headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    project = json.load(response)
                self.assertEqual(len(project["drafts"]), 2)
                with urlopen(root + "/api/projects/" + project["id"], timeout=5) as response:
                    self.assertEqual(json.load(response), project)
            finally:
                server.shutdown()
                thread.join()
