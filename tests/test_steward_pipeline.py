import tempfile
import time
from pathlib import Path
from threading import RLock
import unittest

from se_agent import synthesis
from se_agent.intake import _mock_agent as intake_model
from se_agent.providers import LLMResult
from se_agent.steward_pipeline import StewardPipeline
from se_agent.store import Store
from se_agent.worker import Worker
from se_agent.workspace import ProjectReviews, Workspace
from test_synthesis import handoff


class StewardPipelineTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.reviews = ProjectReviews(self.store)
        self.workspace = Workspace(self.store, self.reviews, Worker(mode="mock"))
        self.agent = None
        self.pipeline = StewardPipeline(self.workspace, lambda: self.agent, RLock())

    def tearDown(self):
        for _ in range(80):
            if not self.pipeline.threads:
                break
            time.sleep(.03)
        self.workspace.dispatch.close()
        self.store.close()

    def wait(self, job, expected="completed", timeout=18):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            result = self.pipeline.public(job["id"])
            if result["status"] == expected:
                return result
            if result["status"] == "failed":
                self.fail(result["error"])
            time.sleep(.05)
        self.fail(result)

    def test_one_requirement_message_completes_without_human_confirmation(self):
        job = self.pipeline.start("# 团队图文分享\n\n- 用户可以创建并浏览图文笔记。", {})
        done = self.wait(job)
        review = self.reviews.get(done["project_id"])
        spec = review["versions"][-1]
        self.assertEqual(spec["confirmed_by"], "steward")
        self.assertTrue(all(o["resolution"] for o in spec["open_items"]))
        self.assertEqual(done["execution"]["done"], done["execution"]["total"])
        self.assertEqual([m["role"] for m in done["messages"]].count("user"), 1)
        self.assertNotIn("drafts", done)
        self.assertEqual(len(done["groups"]), 2)
        self.assertEqual(done["summary"]["mode"], "mock")
        with self.store._lock:
            origins = list(self.store.db.execute("SELECT actor_id FROM review_records"))
        self.assertTrue(origins)
        self.assertTrue(all(row["actor_id"] == "steward" for row in origins))

    def test_optional_confirmation_can_be_removed_without_restarting(self):
        job = self.pipeline.start("# 做一个待办应用", {"gates":["plan"], "group_count":3,
            "prompts":["A：先做清单与排序"]})
        paused = self.wait(job, "gate")
        self.assertEqual(paused["gate"], "plan")
        self.assertEqual(paused["groups"][0]["prompt"], "A：先做清单与排序")
        self.assertEqual(len(paused["groups"]), 3)
        self.assertIsNone(paused["run_id"])
        self.pipeline.set_gates(job["id"], [])
        done = self.wait(job)
        self.assertIsNotNone(done["run_id"])

    def test_existing_draft_uses_model_to_resolve_conflicts_without_handoff_forms(self):
        project = self.reviews.create(handoff())
        id_ = project["id"]
        self.reviews.change(id_, "evaluate", {})
        self.reviews.change(id_, "propose", {"selection":"merge","expected_version":0})
        calls = []

        def model(stage, context):
            calls.append(stage)
            if stage == "steward_decisions":
                return {"decisions": [{"id":item["id"],"choice":"停用后拒绝新预约，已有预约保留。",
                    "reason":"不改变已经受理的预约。"} for item in context["issues"]]}
            if stage == "steward_plan":
                return {"tasks": context["tasks"]}
            if stage == "steward_review":
                return {"approved":True,"reason":"产物与当前任务对应。"}
            raise AssertionError(stage)

        self.agent = model
        done = self.wait(self.pipeline.start(None, {"mode":"mock"}, id_))
        self.assertEqual(done["project_id"], id_)
        self.assertIn("steward_decisions", calls)
        self.assertIn("steward_review", calls)
        self.assertTrue(all(o["resolution"] for o in self.reviews.get(id_)["versions"][-1]["open_items"]))

    def test_chat_revises_project_without_replacing_existing_artifacts(self):
        first = self.wait(self.pipeline.start("做一个待办应用，可以新增任务。", {}))
        second = self.wait(self.pipeline.chat(first["id"], "加上任务截止日期。"))
        self.assertEqual(second["phase"], 2)
        self.assertNotEqual(second["project_id"], first["project_id"])
        self.assertIn("加上任务截止日期", second["markdown"])
        self.assertEqual(self.pipeline.public(first["id"])["status"], "completed")
        third = self.wait(self.pipeline.chat(second["id"], "把分组改成三组，并发上限设为二。"))
        self.assertEqual(len(third["groups"]), 3)
        self.assertEqual(third["settings"]["parallel"], 2)
        self.assertEqual(third["markdown"], second["markdown"])

    def test_models_design_groups_and_create_git_output(self):
        calls = []

        def model(stage, context):
            calls.append(stage)
            if stage == "steward_groups":
                return {"groups":[{"focus": "快速交付", "prompt": "选最小足够实现"},
                                  {"focus": "易于维护", "prompt": "梳理边界与长期维护"}]}
            if stage in ("idea", "challenge", "draft"):
                return intake_model(stage, context)
            if stage in ("evaluate", "synthesize"):
                return synthesis.mock_agent(stage, context)
            if stage == "steward_decisions":
                return {"decisions":[{"id":i["id"],"choice":"允许发布公开笔记。","reason":"按首版要求实现。"} for i in context["issues"]]}
            if stage == "steward_plan":
                return {"tasks": context["tasks"]}
            if stage == "steward_review":
                return {"approved":True,"reason":"示例代码与任务对应。"}
            raise AssertionError(stage)

        class Executor:
            def run_step(self, request, task, stage, provider):
                return LLMResult("def publish():\n    return True\n", 1, 1)

        self.agent = model
        self.reviews.agent = {"agent":model}
        self.workspace.dispatch.worker = Worker(mode="mock", executor=Executor(), provider=object())
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)/"real-project"
            done = self.wait(self.pipeline.start("- 用户可以发布图文笔记。", {"directory":str(repo),"mode":"real"}))
            data = self.workspace.project(done["project_id"])
            commits = [a for t in data["run"]["tasks"].values() for a in t["artifacts"] if a["kind"]=="git_commit"]
            self.assertTrue(commits)
            self.assertTrue(self.store.verifier_for(done["project_id"]).exists(commits[0]["commit_sha"]))
            self.assertTrue(Path(done["delivery"]["directory"],"workflow.json").is_file())
            self.assertTrue({"steward_groups","idea","evaluate","synthesize","steward_decisions",
                             "steward_plan","steward_review"}.issubset(calls), calls)
