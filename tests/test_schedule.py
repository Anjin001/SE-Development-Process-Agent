"""Scheduling: dependency float, concurrency slots and write contention."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from se_agent.schedule import schedule_plan

SCENARIO = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "v2" / "scenario.json")
    .read_text(encoding="utf-8"))


def scenario_plan():
    return deepcopy(SCENARIO["plan_response"]["plan"])


def by_id(rows):
    return {row["task_id"]: row for row in rows}


class ScenarioTest(unittest.TestCase):
    def test_matches_the_published_scenario_schedule(self):
        self.assertEqual(schedule_plan(scenario_plan()),
                         SCENARIO["plan_response"]["schedule"])

    def test_missing_estimate_returns_no_schedule(self):
        plan = scenario_plan()
        plan["tasks"][2]["estimate_seconds"] = None
        self.assertIsNone(schedule_plan(plan))

    def test_result_is_stable(self):
        plan = scenario_plan()
        self.assertEqual(schedule_plan(plan), schedule_plan(plan))


class ContentionTest(unittest.TestCase):
    def test_concurrency_limit_serialises_independent_tasks(self):
        plan = scenario_plan()
        plan["max_parallel"] = 1
        rows = by_id(schedule_plan(plan))
        self.assertEqual(rows["T2"]["early_start_seconds"], 300)
        self.assertEqual(rows["T3"]["early_start_seconds"], 900)
        self.assertEqual(plan["tasks"][3]["id"], "T4")   # T4 waits for both

    def test_shared_write_path_serialises_tasks_that_could_run_in_parallel(self):
        plan = scenario_plan()
        plan["tasks"][2]["write_paths"] = ["app/reservations.py"]
        rows = by_id(schedule_plan(plan))
        self.assertEqual(rows["T2"]["early_start_seconds"], 300)
        self.assertEqual(rows["T2"]["early_finish_seconds"], 900)
        self.assertEqual(rows["T3"]["early_start_seconds"], 900)

    def test_directory_prefix_is_not_contention(self):
        plan = scenario_plan()
        plan["tasks"][2]["write_paths"] = ["app2/usage.md"]
        rows = by_id(schedule_plan(plan))
        self.assertEqual(rows["T3"]["early_start_seconds"], 300)

    def test_waiting_does_not_move_the_critical_path(self):
        plan = scenario_plan()
        plan["max_parallel"] = 1
        rows = by_id(schedule_plan(plan))
        self.assertTrue(rows["T1"]["critical"])
        self.assertTrue(rows["T2"]["critical"])
        self.assertFalse(rows["T3"]["critical"])
        self.assertTrue(rows["T4"]["critical"])


if __name__ == "__main__":
    unittest.main()