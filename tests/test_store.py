"""Run lifecycle: eight states, lease claiming, reports, review and events."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from se_agent.contracts import (InvalidOutput, InvalidReference, InvalidState,
                                StaleAttempt, VersionConflict)
from se_agent.store import Store

SHA = "a" * 40


class Clock:
    def __init__(self):
        self.moment = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def tick(self, seconds):
        self.moment += timedelta(seconds=seconds)
        return self.moment


def make_tasks(same_path=False):
    shared = "app/shared.py"
    return [
        {"id": "T1", "title": "定义状态", "phase": "prep", "role": "develop",
         "depends_on": [], "write_paths": ["app/status.py"],
         "outputs": [{"id": "OUT-01", "kind": "git_commit", "path": "app/status.py"}],
         "requirement_ids": ["REQ-01"], "decision_ids": ["DEC-01"], "open_item_ids": [],
         "estimate_seconds": 300, "review_required": False,
         "completion_condition": "状态约定可读。"},
        {"id": "T2", "title": "实现预约", "phase": "build", "role": "develop",
         "depends_on": ["T1"],
         "write_paths": [shared if same_path else "app/reservations.py"],
         "outputs": [{"id": "OUT-01", "kind": "git_commit",
                      "path": shared if same_path else "app/reservations.py"}],
         "requirement_ids": ["REQ-01"], "decision_ids": ["DEC-01"], "open_item_ids": [],
         "estimate_seconds": 600, "review_required": True,
         "completion_condition": "预约接口可读。"},
        {"id": "T3", "title": "写说明", "phase": "build", "role": "docs",
         "depends_on": ["T1"],
         "write_paths": [shared if same_path else "docs/usage.md"],
         "outputs": [{"id": "OUT-01", "kind": "text", "path": "docs/usage.md"}],
         "requirement_ids": ["REQ-01"], "decision_ids": ["DEC-01"], "open_item_ids": [],
         "estimate_seconds": 300, "review_required": False,
         "completion_condition": "说明可读。"},
        {"id": "T4", "title": "集成", "phase": "integration", "role": "develop",
         "depends_on": ["T2", "T3"], "write_paths": ["app/integration.py"],
         "outputs": [{"id": "OUT-01", "kind": "git_commit", "path": "app/integration.py"}],
         "requirement_ids": ["REQ-01"], "decision_ids": ["DEC-01"], "open_item_ids": [],
         "estimate_seconds": 300, "review_required": False,
         "completion_condition": "集成提交可读。"},
    ]


def make_spec(open_items=None):
    return {"id": "SPEC-01", "version": 1, "status": "confirmed", "selection": "merge",
            "markdown": "# 规格", "decisions": [{"id": "DEC-01", "choice": "统一状态"}],
            "open_items": open_items if open_items is not None else []}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Store(":memory:", now=self.clock)
        self.project = "PROJECT-01"
        self.store.create_project(self.project)

    def tearDown(self):
        self.store.close()

    def start(self, tasks=None, spec=None, max_parallel=2, mode="auto"):
        self.store.save_spec(self.project, spec or make_spec())
        plan = self.store.create_plan(self.project, spec or make_spec(), SHA, max_parallel,
                                      tasks or make_tasks())
        run = self.store.create_run(self.project, plan["id"], plan["version"], mode)
        return plan, run

    def succeed(self, run_id, request, commit=SHA):
        return self.store.report(run_id, request["attempt_id"], "w1", "succeeded",
                                 artifacts=[{"output_id": "OUT-01", "kind": "git_commit",
                                             "commit_sha": commit}])


class InitialStateTest(StoreTestCase):
    def test_predecessors_start_waiting_and_head_is_ready(self):
        _, run = self.start()
        states = {task_id: task["state"] for task_id, task in run["tasks"].items()}
        self.assertEqual(states["T1"], "ready")
        self.assertEqual(states["T2"], "waiting")
        self.assertEqual(states["T4"], "waiting")
        self.assertEqual(run["tasks"]["T2"]["blockers"][0]["code"], "UPSTREAM_RUNNING")

    def test_one_active_run_per_project(self):
        plan, _ = self.start()
        with self.assertRaises(InvalidState):
            self.store.create_run(self.project, plan["id"], plan["version"])


class DispatchTest(StoreTestCase):
    def test_claim_respects_concurrency_limit(self):
        _, run = self.start(max_parallel=1)
        self.assertEqual(len(self.store.claim(run["id"], "w1")), 1)
        self.assertEqual(self.store.claim(run["id"], "w1"), [])

    def test_overlapping_write_paths_are_serialized(self):
        _, run = self.start(tasks=make_tasks(same_path=True), max_parallel=4)
        first = self.store.claim(run["id"], "w1")
        self.assertEqual([item["task"]["id"] for item in first], ["T1"])
        self.succeed(run["id"], first[0])
        second = self.store.claim(run["id"], "w1")
        self.assertEqual(len(second), 1)
        self.assertEqual(self.store.snapshot(run["id"])["tasks"]["T2"]["state"], "running")

    def test_released_path_lets_the_other_start(self):
        _, run = self.start(tasks=make_tasks(same_path=True), max_parallel=4)
        self.succeed(run["id"], self.store.claim(run["id"], "w1")[0])
        claimed = self.store.claim(run["id"], "w1")
        self.assertEqual([item["task"]["id"] for item in claimed], ["T2"])
        self.clock.tick(5)
        self.store.report(run["id"], claimed[0]["attempt_id"], "w1", "succeeded",
                          artifacts=[{"output_id": "OUT-01", "kind": "git_commit",
                                      "commit_sha": SHA}])
        following = self.store.claim(run["id"], "w1")
        self.assertEqual([item["task"]["id"] for item in following], ["T3"])

    def test_directory_prefix_is_not_a_conflict(self):
        tasks = make_tasks()
        tasks[1]["write_paths"] = ["app/reservations.py"]
        tasks[2]["write_paths"] = ["app2/usage.md"]
        _, run = self.start(tasks=tasks, max_parallel=4)
        self.succeed(run["id"], self.store.claim(run["id"], "w1")[0])
        claimed = self.store.claim(run["id"], "w1")
        self.assertEqual(sorted(item["task"]["id"] for item in claimed), ["T2", "T3"])

    def test_paused_run_hands_out_nothing(self):
        _, run = self.start()
        self.store.set_dispatch(run["id"], paused=True)
        self.assertEqual(self.store.claim(run["id"], "w1"), [])

    def test_input_required_blocks_the_task_until_supplied(self):
        spec = make_spec(open_items=[{"id": "OPEN-01", "question": "停用怎么处理",
                                      "blocking": True}])
        tasks = make_tasks()
        tasks[0]["open_item_ids"] = ["OPEN-01"]
        _, run = self.start(tasks=tasks, spec=spec)
        self.assertEqual(run["tasks"]["T1"]["state"], "input_required")
        self.assertEqual(run["tasks"]["T1"]["missing_input_ids"], ["OPEN-01"])
        self.assertEqual(self.store.claim(run["id"], "w1"), [])
        run = self.store.supply_input(run["id"], "T1", {"OPEN-01": "停用即取消"})
        self.assertEqual(run["tasks"]["T1"]["state"], "ready")


class ReportTest(StoreTestCase):
    def test_success_marks_done_and_appends_contiguous_events(self):
        _, run = self.start(max_parallel=1)
        request = self.store.claim(run["id"], "w1")[0]
        run = self.succeed(run["id"], request)
        self.assertEqual(run["tasks"]["T1"]["state"], "done")
        self.assertEqual(run["tasks"]["T1"]["artifacts"][0]["status"], "approved")
        stream = self.store.events(run["id"])
        self.assertEqual([item["seq"] for item in stream["items"]],
                         list(range(1, len(stream["items"]) + 1)))
        self.assertEqual(stream["items"][0]["type"], "run.started")

    def test_review_required_holds_downstream(self):
        _, run = self.start(max_parallel=4)
        self.succeed(run["id"], self.store.claim(run["id"], "w1")[0])
        claimed = {item["task"]["id"]: item for item in self.store.claim(run["id"], "w1")}
        self.assertIn("T3", claimed)
        run = self.store.report(run["id"], claimed["T2"]["attempt_id"], "w1", "succeeded",
                                artifacts=[{"output_id": "OUT-01", "kind": "git_commit",
                                            "commit_sha": SHA}])
        self.assertEqual(run["tasks"]["T2"]["state"], "review")
        self.assertEqual(run["tasks"]["T4"]["blockers"][0]["code"], "UPSTREAM_REVIEW")

    def test_failure_blocks_downstream_but_not_independent_branch(self):
        _, run = self.start(max_parallel=4)
        self.succeed(run["id"], self.store.claim(run["id"], "w1")[0])
        claimed = {item["task"]["id"]: item for item in self.store.claim(run["id"], "w1")}
        run = self.store.report(run["id"], claimed["T2"]["attempt_id"], "w1", "failed",
                                error={"code": "EXECUTION_FAILED", "message": "炸了",
                                       "retryable": True, "details": {}})
        self.assertEqual(run["tasks"]["T2"]["state"], "failed")
        self.assertEqual(run["tasks"]["T4"]["state"], "blocked")
        self.assertEqual(run["tasks"]["T3"]["state"], "running")
        run = self.store.report(run["id"], claimed["T3"]["attempt_id"], "w1", "succeeded",
                                artifacts=[{"output_id": "OUT-01", "kind": "text",
                                            "content": "说明"}])
        self.assertEqual(run["tasks"]["T3"]["state"], "done")
        self.assertEqual(run["tasks"]["T4"]["state"], "blocked")

    def test_report_replay_changes_nothing(self):
        _, run = self.start(max_parallel=1)
        request = self.store.claim(run["id"], "w1")[0]
        first = self.succeed(run["id"], request)
        second = self.succeed(run["id"], request)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(first["latest_event_seq"], second["latest_event_seq"])

    def test_undeclared_output_is_rejected(self):
        _, run = self.start(max_parallel=1)
        request = self.store.claim(run["id"], "w1")[0]
        with self.assertRaises(InvalidOutput):
            self.store.report(run["id"], request["attempt_id"], "w1", "succeeded",
                              artifacts=[{"output_id": "OUT-99", "kind": "text",
                                          "content": "x"}])


class LeaseTest(StoreTestCase):
    def test_expired_lease_turns_into_stale_attempt(self):
        _, run = self.start(max_parallel=1)
        request = self.store.claim(run["id"], "w1", lease_seconds=30)[0]
        self.clock.tick(31)
        with self.assertRaises(StaleAttempt) as caught:
            self.succeed(run["id"], request)
        self.assertEqual(caught.exception.details["current_attempt_id"], request["attempt_id"])
        run = self.store.snapshot(run["id"])
        self.assertEqual(run["tasks"]["T1"]["state"], "failed")
        self.assertEqual(run["tasks"]["T1"]["attempts"][0]["error"],
                         '{"code": "WORKER_TIMEOUT", "message": "租约到期"}')

    def test_renew_extends_without_touching_revision(self):
        _, run = self.start(max_parallel=1)
        request = self.store.claim(run["id"], "w1", lease_seconds=30)[0]
        before = self.store.snapshot(run["id"])["revision"]
        self.clock.tick(20)
        self.store.renew(run["id"], request["attempt_id"], "w1", lease_seconds=60)
        self.assertEqual(self.store.snapshot(run["id"])["revision"], before)
        self.clock.tick(30)
        run = self.succeed(run["id"], request)
        self.assertEqual(run["tasks"]["T1"]["state"], "done")

    def test_expire_leases_releases_the_slot(self):
        _, run = self.start(max_parallel=1)
        self.store.claim(run["id"], "w1", lease_seconds=10)
        self.clock.tick(11)
        self.assertEqual(self.store.expire_leases(run["id"]), 1)
        run = self.store.retry(run["id"], "T1")
        self.assertEqual(run["tasks"]["T1"]["state"], "ready")


class ReviewTest(StoreTestCase):
    def reach_review(self):
        _, run = self.start(max_parallel=4)
        self.succeed(run["id"], self.store.claim(run["id"], "w1")[0])
        claimed = {item["task"]["id"]: item for item in self.store.claim(run["id"], "w1")}
        self.store.report(run["id"], claimed["T3"]["attempt_id"], "w1", "succeeded",
                          artifacts=[{"output_id": "OUT-01", "kind": "text",
                                      "content": "说明"}])
        run = self.store.report(run["id"], claimed["T2"]["attempt_id"], "w1", "succeeded",
                                artifacts=[{"output_id": "OUT-01", "kind": "git_commit",
                                            "commit_sha": SHA}])
        return run["id"]

    def test_approve_releases_downstream(self):
        run_id = self.reach_review()
        run = self.store.review(run_id, "T2", "approve", {"OUT-01": 1}, note="可以")
        self.assertEqual(run["tasks"]["T2"]["state"], "done")
        self.assertEqual(run["tasks"]["T4"]["state"], "ready")

    def test_revise_creates_next_version_and_downstream_reads_it(self):
        run_id = self.reach_review()
        run = self.store.review(run_id, "T2", "revise", {"OUT-01": 1},
                                revisions={"OUT-01": {"content": "人工修订",
                                                      "commit_sha": "b" * 40}},
                                note="补状态", actor_id="reviewer")
        versions = [item["version"] for item in run["tasks"]["T2"]["artifacts"]]
        self.assertEqual(versions, [1, 2])
        self.assertEqual(run["tasks"]["T2"]["artifacts"][-1]["origin"], "human")
        request = {item["task"]["id"]: item for item in
                   self.store.claim(run_id, "w1")}["T4"]
        self.assertEqual(request["inputs"][0]["version"], 2)
        self.assertEqual(request["inputs"][0]["commit_sha"], "b" * 40)

    def test_reject_requires_a_note_and_blocks_downstream(self):
        run_id = self.reach_review()
        with self.assertRaises(InvalidReference):
            self.store.review(run_id, "T2", "reject", {"OUT-01": 1}, note="  ")
        run = self.store.review(run_id, "T2", "reject", {"OUT-01": 1}, note="不合规")
        self.assertEqual(run["tasks"]["T2"]["state"], "failed")
        self.assertEqual(run["tasks"]["T4"]["state"], "blocked")

    def test_stale_expected_version_is_a_conflict(self):
        run_id = self.reach_review()
        with self.assertRaises(VersionConflict) as caught:
            self.store.review(run_id, "T2", "approve", {"OUT-01": 2})
        self.assertEqual(caught.exception.details["current_versions"], {"OUT-01": 1})


class ManualDispatchTest(StoreTestCase):
    def test_claim_without_step_hands_out_nothing(self):
        _, run = self.start(mode="manual")
        self.assertEqual(self.store.claim(run["id"], "w1"), [])

    def test_step_fixes_a_batch_that_claim_consumes(self):
        _, run = self.start(mode="manual", max_parallel=1)
        self.assertEqual(self.store.step(run["id"]), ["T1"])
        with self.assertRaises(InvalidState):
            self.store.step(run["id"])           # the queue is still open
        handed = self.store.claim(run["id"], "w1")
        self.assertEqual([item["attempt_id"] for item in handed], ["T1.1"])
        self.assertEqual(self.store.claim(run["id"], "w1"), [])
        with self.assertRaises(InvalidState):
            self.store.step(run["id"])           # nothing ready while T1 runs

    def test_manual_step_does_not_bypass_the_concurrency_limit(self):
        _, run = self.start(mode="manual", max_parallel=1)
        self.assertEqual(len(self.store.step(run["id"])), 1)

    def test_pausing_clears_unconsumed_slots(self):
        _, run = self.start(mode="manual")
        self.store.step(run["id"])
        self.store.set_dispatch(run["id"], paused=True)
        self.store.set_dispatch(run["id"], paused=False)
        self.assertEqual(self.store.claim(run["id"], "w1"), [])

    def test_auto_mode_needs_no_step(self):
        _, run = self.start(mode="auto")
        with self.assertRaises(InvalidState):
            self.store.step(run["id"])
        self.assertEqual(len(self.store.claim(run["id"], "w1")), 1)


class RestartTest(unittest.TestCase):
    def test_state_survives_reopening_the_database(self):
        path = Path(__file__).with_name("_run_state.db")
        if path.exists():
            path.unlink()
        clock = Clock()
        try:
            first = Store(path, now=clock)
            first.create_project("PROJECT-01")
            spec = make_spec()
            first.save_spec("PROJECT-01", spec)
            plan = first.create_plan("PROJECT-01", spec, SHA, 2, make_tasks())
            run = first.create_run("PROJECT-01", plan["id"], plan["version"], "auto")
            request = first.claim(run["id"], "w1")[0]
            first.close()

            second = Store(path, now=clock)
            reopened = second.snapshot(run["id"])
            self.assertEqual(reopened["tasks"]["T1"]["state"], "running")
            self.assertEqual(reopened["revision"], run["revision"] + 1)
            done = second.report(run["id"], request["attempt_id"], "w1", "succeeded",
                                 artifacts=[{"output_id": "OUT-01", "kind": "git_commit",
                                             "commit_sha": SHA}])
            self.assertEqual(done["tasks"]["T1"]["state"], "done")
            second.close()
        finally:
            for leftover in path.parent.glob("_run_state.db*"):
                leftover.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()