"""The plan and run HTTP surface: paths, responses, conflicts and replay."""
import json
import threading
import unittest
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from se_agent.api_v2 import Router
from se_agent.api_v2_http import make_server
from se_agent.store import Store

SHA = "a" * 40
PROJECT = "PROJECT-01"
PLANS = f"/api/v2/projects/{PROJECT}/plans"
RUNS = f"/api/v2/projects/{PROJECT}/runs"


def fixed_clock():
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return lambda: moment


def tasks():
    base = {"phase": "development", "role": "develop", "requirement_ids": ["REQ-01"],
            "decision_ids": ["DEC-01"], "open_item_ids": [], "review_required": False}
    return [
        dict(base, id="T1", title="定义状态", depends_on=[],
             write_paths=["app/status.py"],
             outputs=[{"id": "OUT-01", "kind": "git_commit", "path": "app/status.py"}],
             estimate_seconds=300, completion_condition="状态可读。"),
        dict(base, id="T2", title="实现预约", depends_on=["T1"],
             write_paths=["app/reservations.py"],
             outputs=[{"id": "OUT-01", "kind": "git_commit",
                       "path": "app/reservations.py"}],
             estimate_seconds=600, review_required=True,
             completion_condition="预约可读。"),
        dict(base, id="T3", title="写说明", depends_on=["T1"], role="docs",
             write_paths=["docs/usage.md"],
             outputs=[{"id": "OUT-01", "kind": "text", "path": "docs/usage.md"}],
             estimate_seconds=300, completion_condition="说明可读。"),
        dict(base, id="T4", title="集成", depends_on=["T2", "T3"],
             write_paths=["app/integration.py"],
             outputs=[{"id": "OUT-01", "kind": "git_commit",
                       "path": "app/integration.py"}],
             estimate_seconds=300, completion_condition="集成可读。"),
    ]


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:", now=fixed_clock())
        self.router = Router(self.store)
        self.store.create_project(PROJECT)
        self.store.save_spec(PROJECT, {"id": "SPEC-01", "version": 1,
                                       "status": "confirmed", "markdown": "# 规格",
                                       "decisions": [{"id": "DEC-01", "choice": "统一"}],
                                       "open_items": []})

    def tearDown(self):
        self.store.close()

    def call(self, method, path, body=None, key=None, query=None):
        headers = {"Idempotency-Key": key} if key else {}
        return self.router.handle(method, path, body, headers, query)

    def create_plan(self, key="plan-1", **overrides):
        body = {"spec_ref": {"id": "SPEC-01", "version": 1}, "base_commit": SHA,
                "max_parallel": 2, "tasks": tasks()}
        body.update(overrides)
        return self.call("POST", PLANS, body, key)

    def create_run(self, key="run-1"):
        status, plan = self.create_plan()
        self.assertEqual(status, 201)
        return self.call("POST", RUNS, {"plan_ref": {"id": plan["plan"]["id"],
                                                    "version": plan["plan"]["version"]},
                                        "mode": "manual"}, key)


class PlanApiTest(ApiTestCase):
    def test_plan_creation_returns_plan_and_schedule(self):
        status, payload = self.create_plan()
        self.assertEqual(status, 201)
        self.assertEqual(payload["plan"]["id"], "PLAN-01")
        self.assertEqual([row["task_id"] for row in payload["schedule"]],
                         ["T1", "T2", "T3", "T4"])
        self.assertEqual(payload["schedule"][3]["early_finish_seconds"], 1200)

    def test_plan_without_tasks_is_rejected(self):
        status, payload = self.call("POST", PLANS,
                                    {"spec_ref": {"id": "SPEC-01", "version": 1},
                                     "base_commit": SHA}, "plan-x")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "INVALID_REFERENCE")

    def test_unknown_spec_version_is_not_found(self):
        status, payload = self.call("POST", PLANS,
                                    {"spec_ref": {"id": "SPEC-01", "version": 9},
                                     "base_commit": SHA, "tasks": tasks()}, "plan-y")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    def test_read_one_version(self):
        self.create_plan()
        status, payload = self.call("GET", f"{PLANS}/PLAN-01/versions/1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["plan"]["version"], 1)

    def test_patch_creates_the_next_version(self):
        self.create_plan()
        status, payload = self.call("PATCH", f"{PLANS}/PLAN-01",
                                    {"expected_version": 1, "max_parallel": 1},
                                    "patch-1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["plan"]["version"], 2)
        self.assertEqual(payload["schedule"][2]["early_start_seconds"], 900)

    def test_patch_with_stale_version_conflicts(self):
        self.create_plan()
        status, payload = self.call("PATCH", f"{PLANS}/PLAN-01",
                                    {"expected_version": 0, "max_parallel": 1},
                                    "patch-2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "VERSION_CONFLICT")
        self.assertEqual(payload["error"]["details"]["current_version"], 1)

    def test_patch_with_a_cycle_is_rejected_with_the_loop(self):
        self.create_plan()
        broken = tasks()
        broken[0]["depends_on"] = ["T4"]
        status, payload = self.call("PATCH", f"{PLANS}/PLAN-01",
                                    {"expected_version": 1, "tasks": broken}, "patch-3")
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "PLAN_CYCLE")
        self.assertEqual(payload["error"]["details"]["cycle"],
                         ["T1", "T2", "T4", "T1"])


class RunApiTest(ApiTestCase):
    def test_start_binds_the_latest_plan_version(self):
        status, payload = self.create_run()
        self.assertEqual(status, 201)
        self.assertEqual(payload["state"], "active")
        self.assertEqual(payload["plan_ref"], {"id": "PLAN-01", "version": 1})
        self.assertEqual(payload["tasks"]["T1"]["state"], "ready")

    def test_start_with_an_old_plan_version_conflicts(self):
        self.create_plan()
        status, payload = self.call("POST", RUNS, {"plan_ref": {"id": "PLAN-01",
                                                                "version": 0}},
                                    "run-old")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "VERSION_CONFLICT")

    def test_run_from_another_project_is_not_found(self):
        _, run = self.create_run()
        status, _ = self.call("GET", f"/api/v2/projects/OTHER/runs/{run['id']}")
        self.assertEqual(status, 404)

    def test_step_dispatches_a_batch_and_pause_stops_it(self):
        _, run = self.create_run()
        status, payload = self.call("POST", f"{RUNS}/{run['id']}/actions/step", {},
                                    "step-1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["dispatched"], ["T1.1"])
        self.call("POST", f"{RUNS}/{run['id']}/actions/pause", {}, "pause-1")
        status, payload = self.call("POST", f"{RUNS}/{run['id']}/actions/step", {},
                                    "step-2")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "INVALID_STATE")

    def test_action_with_stale_revision_conflicts(self):
        _, run = self.create_run()
        status, payload = self.call("POST", f"{RUNS}/{run['id']}/actions/pause",
                                    {"expected_revision": 99}, "pause-old")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["details"]["current_revision"],
                         run["revision"])

    def test_retry_of_a_running_task_is_an_invalid_state(self):
        _, run = self.create_run()
        status, payload = self.call("POST", f"{RUNS}/{run['id']}/actions/retry",
                                    {"task_id": "T1"}, "retry-1")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "INVALID_STATE")

    def test_events_paginate_by_seq(self):
        _, run = self.create_run()
        self.call("POST", f"{RUNS}/{run['id']}/actions/step", {}, "step-events")
        status, page = self.call("GET", f"{RUNS}/{run['id']}/events",
                                 query={"after": "0", "limit": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["has_more"])
        status, rest = self.call("GET", f"{RUNS}/{run['id']}/events",
                                 query={"after": str(page["next_after"]), "limit": "10"})
        self.assertFalse(rest["has_more"])


class IdempotencyTest(ApiTestCase):
    def test_replay_returns_the_first_result(self):
        first = self.create_plan(key="same-key")
        second = self.call("POST", PLANS, {"spec_ref": {"id": "SPEC-01", "version": 1},
                                          "base_commit": SHA, "max_parallel": 2,
                                          "tasks": tasks()}, "same-key")
        self.assertEqual(first, second)
        status, payload = self.call("GET", f"{PLANS}/PLAN-01/versions/1")
        self.assertEqual(status, 200)

    def test_same_key_with_a_different_body_conflicts(self):
        self.create_plan(key="shared")
        status, payload = self.call("POST", PLANS,
                                    {"spec_ref": {"id": "SPEC-01", "version": 1},
                                     "base_commit": SHA, "max_parallel": 3,
                                     "tasks": tasks()}, "shared")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_write_without_a_key_is_rejected(self):
        status, payload = self.call("POST", PLANS,
                                    {"spec_ref": {"id": "SPEC-01", "version": 1},
                                     "base_commit": SHA, "tasks": tasks()})
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["details"]["field"], "Idempotency-Key")


class TransportTest(ApiTestCase):
    def test_stdlib_adapter_serves_the_router(self):
        _, plan = self.create_plan()
        with make_server(self.store, 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{root}{RUNS}",
                    data=json.dumps({"plan_ref": {"id": "PLAN-01", "version": 1},
                                     "mode": "manual"}).encode(),
                    headers={"Content-Type": "application/json",
                             "Idempotency-Key": "http-run"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    payload = json.load(response)
                self.assertEqual(payload["tasks"]["T1"]["state"], "ready")
                with urlopen(f"{root}{RUNS}/{payload['id']}/events?after=0", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("items", json.load(response))
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()