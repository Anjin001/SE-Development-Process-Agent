import copy
import unittest

from se_agent import planner, runner, server
from se_agent.contracts import Conflict
from se_agent.orchestrator import validate


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.project = server.create("# 实验室设备预约\n禁止对停用设备发起新预约。")
        self.change("confirm", decision="merge")

    def change(self, action, **fields):
        server.change(self.project, action, fields)

    def test_human_revision_reaches_downstream(self):
        self.change("configure", version=1, max_parallel=2)
        with self.assertRaises(Conflict):
            self.change("start", version=1)
        self.change("start", version=2)
        self.change("step")
        self.change("step")
        run = server.snapshot(self.project)["run"]
        self.assertEqual(run["tasks"]["T2"]["status"], "review")
        self.assertEqual(run["tasks"]["T3"]["status"], "done")
        self.assertNotIn("T4", run["ready"])
        self.change("review", task_id="T2", version=1, decision="revise", content="人工版本", note="补齐状态规则")
        with self.assertRaises(Conflict):
            self.change("review", task_id="T2", version=1, decision="approve")
        self.change("pause")
        with self.assertRaises(Conflict):
            self.change("step")
        self.change("resume")
        self.change("step")
        run = server.snapshot(self.project)["run"]
        self.assertTrue(run["complete"])
        self.assertIn("T2@v2", run["tasks"]["T4"]["artifacts"][0]["content"])
        self.assertEqual(run["tasks"]["T2"]["artifacts"][1]["content"], "人工版本")

    def test_reject_and_retry(self):
        self.change("start", version=1)
        self.change("step")
        self.change("step")
        self.change("review", task_id="T2", version=1, decision="reject", note="缺少约束")
        self.change("retry", task_id="T2")
        self.change("step")
        task = server.snapshot(self.project)["run"]["tasks"]["T2"]
        self.assertEqual(task["status"], "review")
        self.assertEqual([r["attempt_id"] for r in task["attempts"]], ["T2.1", "T2.2"])
        self.assertEqual(task["artifacts"][-1]["version"], 3)

    def test_cycle(self):
        plan = copy.deepcopy(self.project["plan"])
        plan["tasks"][0]["depends_on"] = ["T4"]
        with self.assertRaisesRegex(ValueError, "依赖成环"):
            validate(plan)
