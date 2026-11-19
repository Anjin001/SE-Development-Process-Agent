import json
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
                return {"modules": []}
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

    def test_other_modules_finish_when_one_exhausts_retries(self):
        class OneFailingModule(Worker):
            def execute(self, request):
                result = super().execute(request)
                if request["task"]["title"] == "图文笔记发布":
                    result.update(status="failed", artifacts=[], error={
                        "code": "OUTPUT_INVALID", "message": "笔记模块语法校验失败",
                        "retryable": False, "details": {},
                    })
                return result

        self.workspace.dispatch.worker = OneFailingModule(mode="mock")
        job = self.pipeline.start("做一个图文笔记应用，用户可以发布笔记，关注点赞作者",
                                  {"mode": "mock", "parallel": 1})
        result = self.wait(job, "failed")
        run = self.workspace.run(result["project_id"], result["run_id"])
        self.assertEqual(run["tasks"]["T2"]["state"], "failed")
        self.assertEqual(len(run["tasks"]["T2"]["attempts"]), 2)
        self.assertEqual([run["tasks"][key]["state"] for key in ("T3", "T4", "T5")], ["done"]*3)
        self.assertEqual(run["tasks"]["T6"]["state"], "blocked")

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
        replan = self.pipeline.chat(done["id"], "重新开始任务编排，让我单步调试")
        checkpoint = self.wait(replan, "gate")
        self.assertEqual(checkpoint["id"], done["id"])
        self.assertEqual(checkpoint["project_id"], done["project_id"])
        self.assertEqual(checkpoint["gate"], "plan")
        self.pipeline.chat(checkpoint["id"], "继续", "continue")
        for _ in range(100):
            debug = self.pipeline.public(checkpoint["id"])
            if debug["run_id"]:
                break
            time.sleep(.05)
        run = self.workspace.run(debug["project_id"], debug["run_id"])
        self.assertEqual(run["mode"], "manual")
        self.assertTrue(all(not task["attempts"] for task in run["tasks"].values()))
        self.workspace.action(debug["project_id"], run["id"], "advance",
                              {"expected_revision":run["revision"], "single":True})
        for _ in range(100):
            run = self.workspace.run(debug["project_id"], run["id"])
            if run["tasks"]["T1"]["state"] == "review":
                break
            time.sleep(.05)
        self.assertEqual(run["tasks"]["T1"]["state"], "review")
        self.assertEqual(sum(len(task["attempts"]) for task in run["tasks"].values()), 1)
        self.wait(self.pipeline.chat(debug["id"], "暂停", "pause"), "paused")

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
                return {"modules": []}
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
                return {"modules": []}
            if stage == "steward_review":
                if "tasks" in context and not any("测试修复条件" in t["completion_condition"] for t in context["tasks"]):
                    return {"approved":False,"reason":"T2 缺少异常场景的条件。"}
                return {"approved":True,"reason":"示例代码与任务对应。"}
            if stage == "steward_plan_repair":
                self.assertIn("T2 缺少异常场景", context["review_feedback"])
                task = next(t for t in context["tasks"] if t["id"]=="T2")
                return {"changes":[{"id":"T2","completion_condition":task["completion_condition"]+"测试修复条件"}]}
            raise AssertionError(stage)

        slices = []
        class Executor:
            def run_step(self, request, task, stage, provider):
                outputs = task["outputs"]
                path = outputs[0]["path"]
                if path=="docs/api-contract.json":
                    module = request.get("contract_slice")
                    self_check.assertIsNotNone(module,"A monolithic contract would hit the observed 4096-token cap")
                    self_check.assertLessEqual(stage["max_output_tokens"],2048)
                    slices.append(module["name"])
                    route = "/api/admin/notes" if module["name"] == "notes" else "/api/"+module["name"]
                    text = json.dumps({"endpoints":[{"method":"POST","path":route,
                        "handler":"create_"+module["name"],"request":["title"],"response":["id"]}]})
                elif path=="docs/technical-handoff.md":
                    self_check.assertIn("docs/api-contract.json",request["generated_files"])
                    text = "接口以 docs/api-contract.json 为准。"
                elif path.startswith("app/") and path.endswith("routes.py"):
                    self_check.assertIn("docs/api-contract.json",request["upstream_files"])
                    text = "def routes():\n    return []\n"
                else:
                    text = "def publish():\n    return True\n"
                return LLMResult(text, 1, 1)
        self_check = self

        self.agent = model
        self.reviews.agent = {"agent":model}
        self.workspace.dispatch.worker = Worker(mode="mock", executor=Executor(), provider=object())
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)/"real-project"
            job = self.pipeline.start("做一个图文笔记应用，用户可以发布笔记，关注点赞作者",
                                      {"directory":str(repo), "mode":"real", "gates":["verification"]})
            gate = self.wait(job, "gate")
            self.assertEqual(gate["gate"], "verification")
            running = self.workspace.project(gate["project_id"])
            pending = next(t for t in running["run"]["tasks"].values() if t["state"] == "review")
            feedback = "发布失败场景缺少响应断言，补充验证后再交付。"
            run = running["run"]
            untouched = run["tasks"]["T3"]["artifacts"]
            run = self.workspace.action(gate["project_id"], run["id"], "verification-return",
                {"expected_revision":run["revision"], "task_id":pending["task_id"],
                 "target_task_id":"T2", "note":feedback})
            self.assertEqual(run["mode"], "manual")
            self.assertEqual(run["tasks"]["T2"]["state"], "ready")
            self.assertEqual(run["tasks"]["T3"]["artifacts"], untouched)
            self.assertTrue(all(a["status"] == "superseded" for a in run["tasks"][pending["task_id"]]["artifacts"]))
            self.assertTrue(any(feedback in str(event["payload"]) for event in self.store.events(run["id"])["items"]))
            self.pipeline.chat(job["id"], "继续", "continue")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                run = self.workspace.run(gate["project_id"], run["id"])
                if run["state"] == "completed":
                    break
                if not any(task["state"] == "running" for task in run["tasks"].values()):
                    for task in run["tasks"].values():
                        if task["state"] == "review":
                            self.workspace.action(gate["project_id"], run["id"], "review", {
                                "expected_revision":run["revision"], "task_id":task["task_id"], "decision":"approve",
                                "expected_versions":{a["output_id"]:a["version"] for a in task["artifacts"] if a["status"]=="draft"},
                                "note":"测试夹具确认返工与下游依赖已重新放行。"})
                            break
                    else:
                        if any(task["state"] == "ready" for task in run["tasks"].values()):
                            self.workspace.dispatch.advance(run["id"], single=True)
                time.sleep(.08)
            done = self.wait(job)
            data = self.workspace.project(done["project_id"])
            commits = [a for t in data["run"]["tasks"].values() for a in t["artifacts"] if a["kind"]=="git_commit"]
            self.assertTrue(commits)
            plan = data["planning"]["plan"]
            modules = [t for t in plan["tasks"] if t["phase"]=="development"]
            integration = next(t for t in plan["tasks"] if t["phase"] == "integration")
            verification = next(t for t in plan["tasks"] if t["phase"] == "verification")
            delivery = next(t for t in plan["tasks"] if t["phase"] == "documentation")
            self.assertTrue(set(t["id"] for t in modules) <= set(integration["depends_on"]))
            self.assertEqual(verification["depends_on"], [integration["id"]])
            self.assertEqual(delivery["depends_on"], [verification["id"]])
            self.assertGreaterEqual(len(modules),4)
            self.assertCountEqual(slices,["notes","follows","reactions"])
            self.assertTrue(all(t["depends_on"]==["T1"] and len(t["outputs"])>=3 for t in modules))
            self.assertEqual([t["id"] for t in modules if "测试修复条件" in t["completion_condition"]], ["T2"])
            self.assertEqual(calls.count("steward_plan_repair"), 1)
            verifier = self.store.verifier_for(done["project_id"])
            final_commit = done["delivery"]["git_commit"]
            for t in modules:
                self.assertTrue(all(verifier.is_ancestor(a["commit_sha"],final_commit)
                                    for a in data["run"]["tasks"][t["id"]]["artifacts"] if a["status"]=="approved"))
            for expected in ("docs/technical-handoff.md","app/main.py","db/schema.sql"):
                self.assertTrue(verifier._git("show",final_commit+":"+expected).strip())
            contract = json.loads(verifier._git("show",final_commit+":docs/api-contract.json"))
            self.assertEqual(set(contract["modules"]),{"notes","follows","reactions"})
            self.assertEqual(contract["modules"]["notes"]["endpoints"][0]["path"], "/api/admin/notes")
            rebuilt = self.wait(self.pipeline.chat(done["id"],"按模块重新拆分"),"gate")
            self.assertEqual(rebuilt["project_id"],done["project_id"])
            self.assertEqual(rebuilt["gate"],"plan")
            self.assertIsNone(rebuilt["run_id"])
            self.assertTrue(verifier.exists(final_commit))
            self.assertTrue(Path(done["delivery"]["directory"],"workflow.json").is_file())
            self.assertTrue({"steward_groups","idea","evaluate","synthesize","steward_decisions",
                             "steward_plan","steward_review"}.issubset(calls), calls)
