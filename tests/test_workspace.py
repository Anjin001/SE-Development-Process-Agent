"""Regressions at the boundaries between the independently developed modules."""
from copy import deepcopy
import time
import unittest
import tempfile
from pathlib import Path

from se_agent.providers import LLMResult
from se_agent.workspace_experience import Progress, help_answer, steward_question

from se_agent.contracts import DomainError
from se_agent.store import Store
from se_agent.workspace import ProjectReviews, Workspace
from se_agent.worker import Worker
from test_synthesis import handoff


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.db = Store()
        self.reviews = ProjectReviews(self.db)
        self.workspace = Workspace(self.db, self.reviews, Worker(mode="mock"))
        self.project = self.reviews.create(handoff())
        self.id = self.project["id"]
        self.reviews.change(self.id, "evaluate", {})
        self.reviews.change(self.id, "propose", {"selection": "merge", "expected_version": 0})
        self.confirmed = self.reviews.change(self.id, "confirm", {"expected_version": 1})

    def tearDown(self):
        self.workspace.dispatch.close()
        self.db.close()

    def wait_idle(self, run_id):
        for _ in range(100):
            if run_id not in self.workspace.dispatch.busy:
                return self.workspace.run(self.id, run_id)
            time.sleep(0.02)
        self.fail("Worker did not complete its batch")

    def test_choose_delivery_folder_rebind_plan_and_keep_old_run(self):
        original = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        old = self.db.create_run(self.id, original["id"], original["version"])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "chosen"
            project = self.workspace.configure_target(self.id, str(target), real=True)
            newer = project["planning"]["plan"]
            self.assertEqual(newer["version"], original["version"] + 1)
            self.assertEqual(newer["tasks"], original["tasks"])
            self.assertEqual(project["run"]["plan_ref"]["version"], original["version"])
            self.assertTrue(self.workspace.delivery.verifier(self.id).exists(newer["base_commit"]))
            self.assertTrue((target / ".git").is_dir())
            self.workspace.action(self.id, old["id"], "stop", {"expected_revision": old["revision"]})
            class FakeExecutor:
                def run_step(self, request, task, stage, provider):
                    return LLMResult("def delivered():\n    return True\n", 1, 2)
            self.workspace.dispatch.worker = Worker(mode="mock", provider=object(), executor=FakeExecutor())
            new = self.db.create_run(self.id, newer["id"], newer["version"])
            for task in new["tasks"].values():
                if task["missing_input_ids"]:
                    self.db.supply_input(new["id"], task["task_id"],
                                         {id_: "保留已有预约。" for id_ in task["missing_input_ids"]})
            self.assertEqual(self.db.snapshot(old["id"])["state"], "stopped")
            self.assertEqual(new["plan_ref"]["version"], newer["version"])
            commits = []
            for _ in range(8):
                self.workspace.dispatch.advance(new["id"])
                snapshot = self.wait_idle(new["id"])
                commits = [artifact for task in snapshot["tasks"].values() for artifact in task["artifacts"]
                           if artifact["kind"] == "git_commit"]
                if commits:
                    break
            self.assertTrue(commits, snapshot)
            verifier = self.workspace.delivery.verifier(self.id)
            self.assertTrue(verifier.exists(commits[0]["commit_sha"]))
            self.assertTrue(commits[0]["changed_paths"])
            self.assertIn("def delivered", verifier._git("show", commits[0]["commit_sha"] + ":" + commits[0]["changed_paths"][0]))
            result = self.workspace.delivery.export(self.workspace.project(self.id))
            location = Path(result["directory"])
            self.assertEqual(location.parent, target.resolve())
            self.assertTrue((location / "specification.md").is_file())
            self.assertIn(newer["base_commit"], (location / "workflow.json").read_text(encoding="utf8"))

    def test_steward_preserves_product_decision_and_applies_single_answer(self):
        plan = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        run = self.db.create_run(self.id, plan["id"], plan["version"])
        questions = self.workspace.steward_questions(self.id, run["id"])
        first = questions[0]
        self.assertIn("预约", first["question"])
        self.assertIsNone(first["default"])
        run = self.workspace.action(self.id, run["id"], "steward-defaults", {"expected_revision": run["revision"]})
        self.assertEqual(self.workspace.steward_questions(self.id, run["id"]), questions)
        run = self.workspace.action(self.id, run["id"], "steward-answer",
                                    {"expected_revision": run["revision"], "item_id": first["id"],
                                     "answer": "保留已有预约。"})
        self.assertTrue(all(first["id"] not in run["tasks"][id_]["missing_input_ids"] for id_ in first["tasks"]))
        self.assertIsNotNone(steward_question("OPEN-99", {"question": "请选择单元测试框架。"},
                                              ["T1"], {"items": []})["default"])
        self.assertIsNone(steward_question("OPEN-99", {"question": "选择数据库和权限规则。"},
                                           ["T1"], {"items": []})["default"])

    def test_progress_exposes_milestones_and_help_prefers_workbench_guide(self):
        tracker = Progress()
        tracker.record("RUN-02.T1.1", "生成任务产物", 60)
        self.assertEqual(tracker.read("RUN-02.T1.1")[0]["timeout_seconds"], 60)
        self.assertEqual(help_answer("源码文件保存到哪里？")["source"], "guide")
        result = help_answer("这里能同时打开多少视图？",
                             lambda stage, context: {"answer": "未提供这个信息。"})
        self.assertEqual(result["source"], "model")

    def test_confirmed_spec_to_plan_schedule_and_partial_input(self):
        response = self.workspace.plans.generate(self.confirmed, 1)
        self.assertEqual(len(response["schedule"]), len(response["plan"]["tasks"]))
        affected = [t for t in response["plan"]["tasks"] if t["open_item_ids"]]
        self.assertEqual([t["requirement_ids"] for t in affected], [["REQ-02"]])
        plan = response["plan"]
        run = self.db.create_run(self.id, plan["id"], plan["version"])
        task_id = affected[0]["id"]
        self.assertIn("OPEN-01", run["tasks"][task_id]["missing_input_ids"])
        self.workspace.dispatch.advance(run["id"])
        run = self.wait_idle(run["id"])
        self.assertEqual(run["tasks"]["T1"]["state"], "done")
        request = self.db._spec_context(self.id, 1, affected[0])
        self.assertEqual([item["id"] for item in request["requirements"]], ["REQ-02"])

    def test_edit_plan_roundtrip_and_cycle_rollback(self):
        response = self.workspace.plans.generate(self.confirmed, 1)
        tasks = deepcopy(response["plan"]["tasks"])
        tasks[0]["title"] = "人工核对任务"
        tasks[0]["estimate_seconds"] = 421
        changed = self.workspace.plans.save(self.confirmed, 1, tasks, 1)
        self.assertEqual(changed["plan"]["version"], 2)
        self.assertEqual(changed["plan"]["max_parallel"], 1)
        self.assertIn("421", changed["task_markdown"])
        tasks[0]["depends_on"] = [tasks[-1]["id"]]
        with self.assertRaises(DomainError):
            self.workspace.plans.save(self.confirmed, 2, tasks)
        self.assertEqual(changed, self.workspace.plans.latest(self.id))

    def test_complete_manual_flow_with_human_revised_artifact(self):
        plan = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        plan["tasks"][0]["review_required"] = True
        saved = self.workspace.plans.save(self.confirmed, 1, plan["tasks"])["plan"]
        run = self.db.create_run(self.id, saved["id"], saved["version"])
        run_id = run["id"]
        self.workspace.dispatch.advance(run_id)
        run = self.wait_idle(run_id)
        self.assertEqual(run["tasks"]["T1"]["state"], "review")
        artifact = run["tasks"]["T1"]["artifacts"][0]
        self.db.review(run_id, "T1", "revise", {artifact["output_id"]: artifact["version"]},
                       {artifact["output_id"]: {"content": "人工补充的接口约束"}})
        for _ in range(20):
            run = self.workspace.run(self.id, run_id)
            if run["state"] == "completed":
                break
            for task in run["tasks"].values():
                if task["missing_input_ids"]:
                    self.db.supply_input(run_id, task["task_id"],
                                         {key: "人工明确的处置" for key in task["missing_input_ids"]})
                if task["state"] == "review":
                    self.db.review(run_id, task["task_id"], "approve",
                                   {a["output_id"]: a["version"] for a in task["artifacts"] if a["status"] == "draft"})
            if self.workspace.run(self.id, run_id)["state"] == "completed":
                break
            self.workspace.dispatch.advance(run_id)
            self.wait_idle(run_id)
        self.assertEqual(self.workspace.run(self.id, run_id)["state"], "completed")
        versions = self.workspace.run(self.id, run_id)["tasks"]["T1"]["artifacts"]
        self.assertTrue(any(a["origin"] == "human" and a["content"] == "人工补充的接口约束" for a in versions))

    def test_multiple_projects_can_execute_without_identifier_collisions(self):
        plan1 = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        run1 = self.db.create_run(self.id, plan1["id"], plan1["version"])
        self.workspace.dispatch.advance(run1["id"])
        self.wait_idle(run1["id"])
        other = self.reviews.create(handoff())
        self.reviews.change(other["id"], "evaluate", {})
        self.reviews.change(other["id"], "propose", {"selection": "merge", "expected_version": 0})
        confirmed = self.reviews.change(other["id"], "confirm", {"expected_version": 1})
        plan2 = self.workspace.plans.generate(confirmed, 1)["plan"]
        run2 = self.db.create_run(other["id"], plan2["id"], plan2["version"])
        self.assertNotEqual(run1["id"], run2["id"])
        self.workspace.dispatch.advance(run2["id"])
        for _ in range(100):
            if run2["id"] not in self.workspace.dispatch.busy:
                break
            time.sleep(0.02)
        self.assertEqual(self.workspace.run(other["id"], run2["id"])["tasks"]["T1"]["state"], "done")

    def test_worker_context_stays_on_the_confirmed_requirement_version(self):
        plan = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        db = self.db.db
        db.execute("INSERT INTO requirements VALUES (?,?,?,?)", (self.id, 2, "新需求", "now"))
        db.execute("INSERT INTO requirement_items VALUES (?,?,?,?,?,?,?,?)",
                   (self.id, 2, "REQ-01", "新条目", "新内容", "正文", 1, 1))
        context = self.db._spec_context(self.id, 1, plan["tasks"][0])
        self.assertNotEqual(context["requirements"][0]["text"], "新内容")
        invalid = deepcopy(plan["tasks"])
        invalid[0]["requirement_ids"] = ["REQ-999"]
        with self.assertRaises(DomainError):
            self.workspace.plans.save(self.confirmed, 1, invalid)

    def test_every_approved_output_is_supplied_to_the_next_worker(self):
        plan = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        first = plan["tasks"][0]
        first["outputs"].append(dict(id="OUT-02", kind="text", path="docs/second.md"))
        first["write_paths"].append("docs/second.md")
        saved = self.workspace.plans.save(self.confirmed, 1, plan["tasks"])["plan"]
        run = self.db.create_run(self.id, saved["id"], saved["version"])
        self.workspace.dispatch.advance(run["id"])
        self.wait_idle(run["id"])
        self.db.step(run["id"])
        request = self.db.claim(run["id"], "boundary-check")[0]
        self.assertEqual({a["output_id"] for a in request["inputs"]}, {"OUT-01", "OUT-02"})
        self.assertTrue(all(a["task_id"] == "T1" and a["content"] for a in request["inputs"]))

    def test_project_and_plan_survive_a_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace.db"
            first = Store(path)
            reviews = ProjectReviews(first)
            imported = reviews.create(handoff())
            reviews.change(imported["id"], "evaluate", {})
            reviews.change(imported["id"], "propose", {"selection": "merge", "expected_version": 0})
            confirmed = reviews.change(imported["id"], "confirm", {"expected_version": 1})
            from se_agent.workspace import ProjectPlans
            saved = ProjectPlans(first).generate(confirmed, 1)
            first.close()
            second = Store(path)
            try:
                restored = ProjectReviews(second)
                self.assertEqual(restored.snapshot(restored.get(imported["id"])), confirmed)
                self.assertEqual(ProjectPlans(second).latest(imported["id"]), saved)
                self.assertEqual(restored.version_handoff(imported["id"], confirmed["specification"]["id"], 1)["requirement"],
                                 confirmed["requirement"])
            finally:
                second.close()

    def test_expensive_boundary_result_reaches_per_output_implementation(self):
        calls = []
        class Executor:
            def run_step(self, request, task, step, provider):
                calls.append((step["stage"], request.get("boundary_guidance"), task["outputs"]))
                return LLMResult("确定的实现约束" if step["stage"] == "boundary" else task["outputs"][0]["id"], 1, 1)
        worker = Worker(mode="real", provider=object(), executor=Executor())
        task = dict(role="integrate", outputs=[dict(id="OUT-01"), dict(id="OUT-02")])
        result = worker._run_steps({}, task, worker.router.plan(task), {})
        self.assertEqual(result, {"OUT-01": "OUT-01", "OUT-02": "OUT-02"})
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[1] == "确定的实现约束" and len(call[2]) == 1 for call in calls[1:]))

    def test_automatic_dispatch_reaches_completion(self):
        plan = self.workspace.plans.generate(self.confirmed, 1)["plan"]
        for task in plan["tasks"]:
            task["review_required"] = False
        saved = self.workspace.plans.save(self.confirmed, 1, plan["tasks"])["plan"]
        run = self.db.create_run(self.id, saved["id"], saved["version"], mode="auto")
        for task in run["tasks"].values():
            if task["missing_input_ids"]:
                self.db.supply_input(run["id"], task["task_id"],
                                     {key: "人工补齐的规则" for key in task["missing_input_ids"]})
        for _ in range(150):
            with self.db._lock:
                completed = self.db.snapshot(run["id"])["state"] == "completed"
            if completed:
                break
            time.sleep(0.03)
        self.assertTrue(completed, self.db.snapshot(run["id"]))

    def test_workbench_cards_publish_atomically_and_keep_versioned_evidence(self):
        content = dict(domain="编排", problem="并发覆盖", cause="写路径相同",
                       trigger="同时写入", correction="分批派发", pass_condition="两项任务分批领取",
                       evidence="首次执行记录", tags=["写冲突"], counter_example="任务仅读取",
                       evidence_state="walkthrough")
        with self.assertRaises(DomainError):
            self.workspace.publish_cards(self.id, [{"content": content}, {"content": {}}])
        self.assertEqual(self.workspace.experiences(self.id), [])
        [first] = self.workspace.publish_cards(self.id, [{"content": content}])
        self.workspace.refresh_baseline(self.id)
        second_content = dict(content, evidence="第二次执行记录", evidence_state="production")
        [second] = self.workspace.publish_cards(self.id, [{"ref": first, "content": second_content}])
        self.assertEqual(first["id"], second["id"])
        self.assertGreater(second["version"], first["version"])
        [record] = self.workspace.experiences(self.id)
        self.assertEqual(record["body"]["evidence"], "第二次执行记录")
        self.assertEqual(record["body"]["evidence_state"], "production")
        self.assertEqual(self.workspace.experience_baseline(self.id), [first])

    def test_specific_experience_requires_evidence_and_independent_publication(self):
        body = dict(domain="编排", problem="同名文件被两个任务覆盖", cause="未检查写入路径",
                    trigger="两个任务修改同一目录", correction="串行派发冲突任务",
                    pass_condition="两项任务分批领取", evidence="本次运行的并发领取记录", tags=["写冲突"])
        ref = self.workspace.record_experience(self.id, body)
        record = self.db.memory.read(ref["id"], ref["version"], self.id, "lead-local", "retrospective")
        self.assertEqual(record["body"]["evidence_state"], "pending")
        published = self.workspace.publish_experience(self.id, ref, ref["version"], "已复核复现记录")
        record = self.db.memory.read(published["id"], published["version"], self.id, "lead-local", "retrospective")
        self.assertEqual(record["visibility"], "published")
        self.assertEqual(record["body"]["evidence_state"], "pending")

