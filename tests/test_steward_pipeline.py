import tempfile
import time
from pathlib import Path
from threading import Barrier, Lock, RLock
import unittest

from se_agent import synthesis
from se_agent.agent_client import AgentError
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

    def test_race_overlaps_groups_honors_limit_and_retries_only_failed_group(self):
        meet = Barrier(2)
        lock = Lock()
        active = peak = 0
        first_ideas = {}
        built = []

        def model(stage, context):
            nonlocal active, peak
            if stage == "steward_groups":
                return {"groups":[{"focus":str(i),"prompt":f"从角度 {i} 独立设计。"} for i in range(4)]}
            if stage == "idea":
                group = context["group_id"]
                with lock:
                    active += 1
                    peak = max(peak, active)
                    first_ideas[group] = first_ideas.get(group, 0) + 1
                    number = first_ideas[group]
                try:
                    if group in {"A","B"} and number == 1:
                        meet.wait(timeout=3)
                    if group == "B" and number == 1:
                        raise AgentError("测试限流")
                    return intake_model(stage,context)
                finally:
                    with lock:
                        active -= 1
            if stage == "draft":
                with lock:
                    built.append(context["group_id"])
                return intake_model(stage,context)
            if stage == "challenge":
                return intake_model(stage,context)
            if stage == "steward_decisions":
                return {"decisions":[{"id":item["id"],"choice":"允许创建并完成任务。",
                    "reason":"满足首版明确范围。"} for item in context["issues"]]}
            if stage == "steward_plan":
                return {"tasks": context["tasks"]}
            raise AssertionError(stage)

        self.agent = model
        job = self.pipeline.start("制作待办清单。", {"race_parallel":2,"group_count":4,
                                                      "gates":["plan"],"mode":"mock"})
        failed = self.wait(job, "failed")
        self.assertEqual(peak, 2)
        self.assertEqual({g["id"]:g["status"] for g in failed["groups"]},
                         {"A":"done","B":"failed","C":"done","D":"done"})
        self.assertIn("B 组", failed["error"])
        self.assertIsNone(failed["project_id"])
        self.pipeline.chat(job["id"], "重试")
        ready = self.wait(job, "gate")
        self.assertTrue(all(g["finished"] for g in ready["groups"]))
        self.assertCountEqual(built, ["A","B","C","D"])
        self.assertEqual(first_ideas, {"A":2,"B":3,"C":2,"D":2})

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
        project = self.workspace.project(paused["project_id"])
        plan = project["planning"]["plan"]
        plan["tasks"][0]["title"] = "人工调整后的第一项"
        saved = self.workspace.plans.save(project["review"], plan["version"], plan["tasks"])
        self.pipeline.set_gates(job["id"], [])
        done = self.wait(job)
        actual = self.workspace.project(done["project_id"])["run"]
        self.assertEqual(actual["plan_ref"]["version"], saved["plan"]["version"])
        self.assertEqual(self.workspace.plans.latest(done["project_id"])["plan"]["tasks"][0]["title"],
                         "人工调整后的第一项")

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
