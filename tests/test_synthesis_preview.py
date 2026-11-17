from copy import deepcopy
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_synthesis import handoff
from se_agent import synthesis
from se_agent.synthesis_preview import PreviewError, PreviewStore, make_preview_server


class PreviewStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = PreviewStore()
        self.project = self.store.create(handoff())
        self.id = self.project["id"]
        self.store.change(self.id, "evaluate", {})

    def propose(self, selection="merge", version=0):
        return self.store.change(self.id, "propose", {"expected_version": version, "selection": selection})

    def test_edit_confirm_export_shape_and_history(self):
        first = self.propose()["specification"]
        edited = deepcopy(first)
        edited["open_items"][0]["resolution"] = "取消受影响的已有预约，在预约记录显示取消原因。"
        edited["decisions"].append({"id": "DEC-HUMAN", "scope": "已有预约处置", "choice": edited["open_items"][0]["resolution"],
                                     "reason": "人工明确本次演示范围", "requirement_ids": ["REQ-02"], "draft_ids": ["B"]})
        edited["markdown"] = synthesis.render_spec_markdown(self.project["requirement"], edited["decisions"], edited["open_items"])
        saved = self.store.change(self.id, "save", {"expected_version": 1, **{k: edited[k] for k in ("markdown", "decisions", "open_items")}})
        self.assertEqual(saved["specification"]["version"], 2)
        self.assertEqual(first, self.store.version(self.id, first["id"], 1))
        final = self.store.change(self.id, "confirm", {"expected_version": 2})
        spec = final["specification"]
        self.assertEqual(spec["status"], "confirmed")
        self.assertEqual(spec["confirmed_by"], "lead-demo")
        self.assertTrue(spec["confirmed_at"].endswith("+00:00"))
        self.assertIn("取消原因", spec["markdown"])
        self.assertEqual(spec["requirement_ref"], {"id": self.project["requirement"]["id"], "version": 1})
        self.assertEqual(spec, json.loads(json.dumps(final))["specification"])
        self.assertTrue(any(r["action"] == "修改" and r["version"] == 2 for r in final["review_records"]))

    def test_confirm_preserves_unresolved_blocking_item(self):
        draft = self.propose()["specification"]
        final = self.store.change(self.id, "confirm", {"expected_version": 1})["specification"]
        self.assertEqual(draft["open_items"], final["open_items"])
        self.assertTrue(final["open_items"][0]["blocking"])
        self.assertIsNone(final["open_items"][0]["resolution"])

    def test_confirmed_content_requires_new_draft(self):
        self.propose()
        confirmed = self.store.change(self.id, "confirm", {"expected_version": 1})["specification"]
        saved = self.store.change(self.id, "save", {"expected_version": 1, **{k: confirmed[k] for k in ("markdown", "decisions", "open_items")}})["specification"]
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["status"], "draft")
        self.assertIsNone(saved["confirmed_at"])
        self.assertEqual(self.store.version(self.id, confirmed["id"], 1), confirmed)

    def test_stale_save_and_confirmation_do_not_change_state(self):
        self.propose()
        before = self.store.snapshot(self.store.get(self.id))
        for action in ("save", "confirm", "propose"):
            with self.subTest(action=action), self.assertRaises(PreviewError) as error:
                self.store.change(self.id, action, {"expected_version": 0, "selection": "A"})
            self.assertEqual(error.exception.code, "VERSION_CONFLICT")
            self.assertEqual(error.exception.details["current_version"], 1)
        self.assertEqual(self.store.snapshot(self.store.get(self.id)), before)

    def test_snapshot_and_import_are_copies_and_invalid_save_is_atomic(self):
        data = handoff()
        imported = self.store.create(data)
        data["drafts"].clear()
        imported["requirement"]["items"].clear()
        self.assertEqual(len(self.store.get(imported["id"])["drafts"]), 2)
        self.assertEqual(len(self.store.get(imported["id"])["requirement"]["items"]), 2)
        self.propose()
        before = self.store.snapshot(self.store.get(self.id))
        with self.assertRaises(synthesis.SynthesisError):
            self.store.change(self.id, "save", {"expected_version": 1, "markdown": "只提交正文"})
        self.assertEqual(before, self.store.snapshot(self.store.get(self.id)))

    def test_actual_cly_handoff_end_to_end(self):
        project = self.store.create(handoff("cly-handoff"))
        self.store.change(project["id"], "evaluate", {})
        self.store.change(project["id"], "propose", {"selection": "merge", "expected_version": 0})
        final = self.store.change(project["id"], "confirm", {"expected_version": 1})
        self.assertEqual(final["specification"]["status"], "confirmed")
        self.assertEqual(final["requirement"], project["requirement"])


    def test_steward_inspects_current_edits_without_saving(self):
        before = self.propose()
        spec = before["specification"]
        request = {"expected_version": 1, **{k: deepcopy(spec[k]) for k in ("markdown", "decisions", "open_items")}}
        good = self.store.change(self.id, "inspect", request)
        self.assertEqual(good["version"], 1)
        self.assertTrue(any(f["ref_id"] == "OPEN-01" and "REQ-02" in f["evidence_refs"]
                            for f in good["findings"] if f["code"] == "OPEN_ITEM"))
        request["decisions"][0]["requirement_ids"] = ["REQ-999"]
        invalid = self.store.change(self.id, "inspect", request)
        self.assertEqual(invalid["findings"][0]["severity"], "error")
        self.assertEqual(invalid["findings"][0]["evidence_refs"], ["proposal.decisions[0].requirement_ids"])
        self.assertEqual(before, self.store.snapshot(self.store.get(self.id)))


class PreviewHttpTest(unittest.TestCase):
    def setUp(self):
        self.server = make_preview_server(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def request(self, path, data=None):
        request = Request(self.root + path, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_browser_entry_import_actions_history_and_conflicts(self):
        with urlopen(self.root, timeout=5) as response:
            self.assertIn("评审与方案整合", response.read().decode())
        for fixture in ("conflict", "cly"):
            status, data = self.request('/preview/examples/' + fixture)
            self.assertEqual(status, 200)
            status, project = self.request('/preview/projects', data)
            self.assertEqual(status, 201)
            base = '/preview/projects/' + project["id"]
            self.assertEqual(self.request(base + '/actions/evaluate', {})[0], 200)
            status, snapshot = self.request(base + '/actions/propose', {"selection": "merge", "expected_version": 0})
            self.assertEqual(status, 200)
            spec = snapshot["specification"]
            status, old = self.request(base + f"/specifications/{spec['id']}/versions/1")
            self.assertEqual((status, old), (200, spec))
            self.assertEqual(self.request(base + '/actions/confirm', {"expected_version": 0})[0], 409)
            status, final = self.request(base + '/actions/confirm', {"expected_version": 1})
            self.assertEqual(status, 200)
            self.assertEqual(final["specification"]["status"], "confirmed")
            self.assertEqual(self.request(base)[1], final)

    def test_invalid_requests_return_structured_errors(self):
        for path, body, expected in [('/preview/projects', [], 400),
                                     ('/preview/projects', {}, 422),
                                     ('/preview/projects/not-found', None, 404),
                                     ('/api/v2/projects', {}, 404)]:
            with self.subTest(path=path):
                status, error = self.request(path, body)
                self.assertEqual(status, expected)
                self.assertEqual(set(error["error"]), {"code", "message", "details"})


if __name__ == "__main__":
    unittest.main()
