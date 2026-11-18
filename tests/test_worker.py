"""Worker request shape, adapter loop and derived supervisor findings."""
import unittest
from datetime import datetime, timezone

from se_agent import worker
from se_agent.store import Store

SHA = "a" * 40
PROJECT = "PROJECT-01"


def clock():
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return lambda: moment


def tasks():
    base = {"phase": "development", "role": "develop", "requirement_ids": ["REQ-01"],
            "decision_ids": ["DEC-01"], "open_item_ids": [], "review_required": False}
    return [
        dict(base, id="T1", title="定义状态", depends_on=[], write_paths=["app/status.py"],
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
    ]


def spec(open_items=None):
    return {"id": "SPEC-01", "version": 1, "status": "confirmed",
            "markdown": "# 规格", "decisions": [{"id": "DEC-01", "choice": "统一状态"}],
            "open_items": open_items or []}


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:", now=clock())
        self.store.create_project(PROJECT)

    def tearDown(self):
        self.store.close()

    def start(self, plan_tasks=None, plan_spec=None, max_parallel=2, mode="auto"):
        chosen_spec = plan_spec or spec()
        self.store.save_spec(PROJECT, chosen_spec)
        plan = self.store.create_plan(PROJECT, chosen_spec, SHA, max_parallel,
                                      plan_tasks or tasks())
        run = self.store.create_run(PROJECT, plan["id"], plan["version"], mode)
        return run["id"]


class RequestShapeTest(WorkerTestCase):
    def test_request_carries_the_contract_fields(self):
        run_id = self.start()
        request = self.store.claim(run_id, "w1")[0]
        for field in ("project_id", "run_id", "attempt_id", "task", "spec_ref",
                      "spec_context", "base_commit", "workspace_id", "inputs",
                      "supplied_inputs"):
            self.assertIn(field, request)
        self.assertEqual(request["project_id"], PROJECT)
        self.assertEqual(request["base_commit"], SHA)
        self.assertEqual(request["workspace_id"], f"{run_id}/T1")
        self.assertEqual(request["spec_ref"], {"id": "SPEC-01", "version": 1})
        self.assertEqual([item["id"] for item in request["spec_context"]["decisions"]],
                         ["DEC-01"])

    def test_supplied_input_reaches_the_next_request(self):
        blocking = [{"id": "OPEN-01", "question": "停用怎么处理", "blocking": True}]
        run_id = self.start(plan_spec=spec(blocking),
                            plan_tasks=[dict(tasks()[0], open_item_ids=["OPEN-01"])]
                            + tasks()[1:])
        run = self.store.supply_input(run_id, "T1", {"OPEN-01": "停用即取消预约"})
        self.assertEqual(run["tasks"]["T1"]["state"], "ready")
        request = self.store.claim(run_id, "w1")[0]
        self.assertEqual(request["supplied_inputs"], {"OPEN-01": "停用即取消预约"})

    def test_inputs_are_the_approved_upstream_artifacts(self):
        run_id = self.start()
        first = self.store.claim(run_id, "w1")
        self.store.submit(run_id, worker.simulated(first[0]), "w1")
        second = self.store.claim(run_id, "w1", limit=1)
        self.assertEqual(second[0]["task"]["id"], "T2")
        upstream = second[0]["inputs"]
        self.assertEqual(len(upstream), 1)
        self.assertEqual(upstream[0]["task_id"], "T1")
        self.assertEqual(upstream[0]["output_id"], "OUT-01")
        self.assertEqual(upstream[0]["version"], 1)
        self.assertEqual(upstream[0]["commit_sha"], "0" * 40)
        self.assertEqual(upstream[0]["status"], "approved")


class AdapterTest(WorkerTestCase):
    def test_run_once_drives_the_simulated_executor(self):
        run_id = self.start()
        results = worker.run_once(self.store, worker.simulated, run_id, limit=1)
        self.assertEqual(len(results), 1)
        snapshot = self.store.snapshot(run_id)
        self.assertEqual(snapshot["tasks"]["T1"]["state"], "done")
        attempt = snapshot["tasks"]["T1"]["attempts"][0]
        self.assertEqual(attempt["state"], "succeeded")
        self.assertEqual(attempt["provider"], "mock")

    def test_executor_crash_is_recorded_as_a_failure(self):
        run_id = self.start()

        def explode(request):
            raise RuntimeError("工具链崩了")

        results = worker.run_once(self.store, explode, run_id, limit=1)
        self.assertEqual(results[0]["status"], "failed")
        snapshot = self.store.snapshot(run_id)
        self.assertEqual(snapshot["tasks"]["T1"]["state"], "failed")
        stored = snapshot["tasks"]["T1"]["attempts"][0]["error"]
        self.assertIn("工具链崩了", stored)


class FindingsTest(WorkerTestCase):
    def test_failure_raises_an_error_finding_with_downstream(self):
        run_id = self.start()
        worker.run_once(self.store, lambda request: worker._failure(
            request, "构建失败"), run_id, limit=1)
        findings = self.store.snapshot(run_id)["findings"]
        codes = {item["code"] for item in findings}
        self.assertIn("TASK_FAILED", codes)
        failed = next(item for item in findings if item["code"] == "TASK_FAILED")
        self.assertEqual(failed["severity"], "error")
        self.assertEqual(failed["ref_id"], "T1")
        self.assertEqual(failed["affected_task_ids"], ["T1", "T2", "T3"])

    def test_review_waiting_is_an_info_finding(self):
        run_id = self.start()
        worker.run_once(self.store, worker.simulated, run_id, limit=1)
        worker.run_once(self.store, worker.simulated, run_id)
        findings = self.store.snapshot(run_id)["findings"]
        review = next(item for item in findings if item["code"] == "REVIEW_WAITING")
        self.assertEqual(review["severity"], "info")
        self.assertEqual(review["ref_id"], "T2")
        self.assertEqual(review["affected_task_ids"], ["T2"])

    def test_findings_disappear_once_the_problem_does(self):
        run_id = self.start()
        worker.run_once(self.store, worker.simulated, run_id, limit=1)
        self.assertEqual(self.store.snapshot(run_id)["findings"], [])

    def test_usage_and_checkout_commit_are_persisted(self):
        run_id = self.start()
        request = self.store.claim(run_id, "w1")[0]
        result = worker.simulated(request)
        result.update({"provider": "anthropic", "model": "claude-x",
                       "usage": {"input_tokens": 10, "output_tokens": 20},
                       "checkout_commit": "b" * 40})
        self.store.submit(run_id, result, "w1")
        attempt = self.store.snapshot(run_id)["tasks"]["T1"]["attempts"][0]
        self.assertEqual(attempt["provider"], "anthropic")
        self.assertEqual(attempt["model"], "claude-x")
        self.assertEqual(attempt["checkout_commit"], "b" * 40)
        self.assertIn("input_tokens", attempt["usage"])


class DriveTest(WorkerTestCase):
    def test_auto_run_dispatches_until_only_review_remains(self):
        run_id = self.start(mode="auto")
        worker.drive(self.store, worker.simulated, run_id)
        run = self.store.snapshot(run_id)
        self.assertEqual(run["tasks"]["T1"]["state"], "done")
        self.assertEqual(run["tasks"]["T2"]["state"], "review")
        self.assertEqual(run["tasks"]["T3"]["state"], "done")
        self.assertEqual(run["state"], "active")

    def test_manual_run_is_driven_through_step(self):
        run_id = self.start(mode="manual")
        worker.drive(self.store, worker.simulated, run_id)
        run = self.store.snapshot(run_id)
        self.assertEqual(run["tasks"]["T1"]["state"], "done")
        self.assertEqual(run["tasks"]["T2"]["state"], "review")

    def test_drive_finishes_a_single_task_plan(self):
        run_id = self.start(mode="auto", plan_tasks=[tasks()[0]])
        processed = worker.drive(self.store, worker.simulated, run_id)
        self.assertEqual(len(processed), 1)
        self.assertEqual(self.store.snapshot(run_id)["state"], "completed")


if __name__ == "__main__":
    unittest.main()