from copy import deepcopy
import unittest

from se_agent.planner_preview import PlanPreviewStore
from se_agent.synthesis_preview import PreviewError, PreviewStore
from test_synthesis import handoff


class PlannerPreviewTest(unittest.TestCase):
    def setUp(self):
        self.review = PreviewStore()
        self.review_id = self.review.create(handoff())["id"]
        self.review.change(self.review_id, "evaluate", {})
        self.draft = self.review.change(
            self.review_id, "propose", {"expected_version": 0, "selection": "merge"},
        )
        self.confirmed = self.review.change(self.review_id, "confirm", {"expected_version": 1})
        self.planner = PlanPreviewStore()

    def test_requires_current_confirmation_and_preserves_unresolved_requirement(self):
        with self.assertRaises(PreviewError):
            self.planner.generate(self.draft, 1)
        with self.assertRaises(PreviewError):
            self.planner.generate(self.confirmed, 2)
        snapshot = self.planner.generate(self.confirmed, 1)
        self.assertEqual(snapshot["plan"]["spec_ref"], {
            "id": self.confirmed["specification"]["id"], "version": 1,
        })
        affected = [task for task in snapshot["plan"]["tasks"] if task["open_item_ids"]]
        self.assertEqual([task["requirement_ids"] for task in affected], [["REQ-02"]])
        self.assertIn(affected[0]["id"], snapshot["plan"]["tasks"][-1]["depends_on"])

    def test_save_rejects_cycle_and_stale_update_without_losing_edits(self):
        snapshot = self.planner.generate(self.confirmed, 1)
        tasks = deepcopy(snapshot["plan"]["tasks"])
        tasks[0]["depends_on"] = [tasks[-1]["id"]]
        with self.assertRaises(PreviewError) as raised:
            self.planner.save(self.confirmed, 1, tasks)
        self.assertEqual(raised.exception.code, "PLAN_CYCLE")
        self.assertEqual(self.planner.generate(self.confirmed, 1), snapshot)
        tasks[0].update(title="人工修改的任务名称", depends_on=[])
        new = self.planner.save(self.confirmed, 1, tasks)
        self.assertEqual(new["plan"]["version"], 2)
        self.assertIn("人工修改的任务名称", new["task_markdown"])
        with self.assertRaises(PreviewError) as raised:
            self.planner.save(self.confirmed, 1, tasks)
        self.assertEqual(raised.exception.code, "VERSION_CONFLICT")
        self.assertEqual(self.planner.generate(self.confirmed, 1), new)
