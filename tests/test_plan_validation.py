"""v2 plan validation: rules, locatable errors and the scenario contract."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from se_agent import planner
from se_agent.contracts import InvalidOutput, InvalidReference, PlanCycle
from se_agent.orchestrator import validate, validate_plan

SCENARIO = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "v2" / "scenario.json")
    .read_text(encoding="utf-8"))


def example_plan():
    return deepcopy(SCENARIO["plan_response"]["plan"])


def example_spec():
    return deepcopy(SCENARIO["specification"])


def get_task(plan, task_id):
    return next(task for task in plan["tasks"] if task["id"] == task_id)


class PlanValidationTest(unittest.TestCase):
    def assertPlanError(self, exc_type, plan, spec=None, code=None):
        with self.assertRaises(exc_type) as caught:
            validate_plan(plan, example_spec() if spec is None else spec)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status, exc_type.status)
        self.assertTrue(caught.exception.details)
        return caught.exception

    def test_scenario_plan_is_valid(self):
        self.assertIsNone(validate_plan(example_plan(), example_spec()))

    def test_cycle_matches_scenario_error_body(self):
        plan = example_plan()
        get_task(plan, "T1")["depends_on"] = ["T4"]
        error = self.assertPlanError(PlanCycle, plan, code="PLAN_CYCLE")
        self.assertEqual(error.as_body(), SCENARIO["errors"]["plan_cycle"]["body"])
        self.assertEqual(error.details["cycle"][0], error.details["cycle"][-1])

    def test_self_dependency(self):
        plan = example_plan()
        get_task(plan, "T2")["depends_on"].append("T2")
        error = self.assertPlanError(InvalidReference, plan)
        self.assertIn("自身", error.message)

    def test_unknown_predecessor(self):
        plan = example_plan()
        get_task(plan, "T4")["depends_on"] = ["T2", "T9"]
        error = self.assertPlanError(InvalidReference, plan)
        self.assertEqual(error.details["unknown"], ["T9"])

    def test_duplicate_task_id(self):
        plan = example_plan()
        plan["tasks"].append(deepcopy(get_task(plan, "T3")))
        error = self.assertPlanError(InvalidReference, plan)
        self.assertEqual(error.details["task_id"], "T3")

    def test_duplicate_predecessor(self):
        plan = example_plan()
        get_task(plan, "T4")["depends_on"] = ["T2", "T2"]
        self.assertPlanError(InvalidReference, plan)

    def test_absolute_write_path(self):
        plan = example_plan()
        get_task(plan, "T1")["write_paths"] = ["/etc/passwd"]
        self.assertPlanError(InvalidReference, plan)

    def test_drive_letter_write_path(self):
        plan = example_plan()
        get_task(plan, "T1")["write_paths"] = ["C:/work/app.py"]
        self.assertPlanError(InvalidReference, plan)

    def test_parent_segment_rejected(self):
        plan = example_plan()
        get_task(plan, "T1")["write_paths"] = ["app/../../outside.py"]
        error = self.assertPlanError(InvalidReference, plan)
        self.assertIn("..", error.message)

    def test_backslash_rejected(self):
        plan = example_plan()
        get_task(plan, "T1")["write_paths"] = ["app\\status.py"]
        self.assertPlanError(InvalidReference, plan)

    def test_unknown_output_kind(self):
        plan = example_plan()
        get_task(plan, "T1")["outputs"][0]["kind"] = "patch"
        error = self.assertPlanError(InvalidOutput, plan, code="INVALID_OUTPUT")
        self.assertEqual(error.details["kind"], "patch")

    def test_task_needs_an_output(self):
        plan = example_plan()
        get_task(plan, "T1")["outputs"] = []
        self.assertPlanError(InvalidOutput, plan)

    def test_duplicate_output_id(self):
        plan = example_plan()
        get_task(plan, "T1")["outputs"].append({"id": "OUT-01", "kind": "text",
                                                "path": "docs/notes.md"})
        error = self.assertPlanError(InvalidOutput, plan)
        self.assertEqual(error.details["output_id"], "OUT-01")

    def test_git_output_needs_path(self):
        plan = example_plan()
        get_task(plan, "T1")["outputs"][0]["path"] = None
        self.assertPlanError(InvalidOutput, plan)

    def test_text_output_path_may_be_null(self):
        plan = example_plan()
        get_task(plan, "T3")["outputs"][0]["path"] = None
        self.assertIsNone(validate_plan(plan, example_spec()))

    def test_completion_condition_required(self):
        plan = example_plan()
        get_task(plan, "T1")["completion_condition"] = "  "
        self.assertPlanError(InvalidOutput, plan)

    def test_estimate_must_be_positive_or_null(self):
        for bad in (0, -30, True, "300", 1.5):
            plan = example_plan()
            get_task(plan, "T1")["estimate_seconds"] = bad
            self.assertPlanError(InvalidReference, plan)

    def test_estimate_null_is_allowed(self):
        plan = example_plan()
        get_task(plan, "T1")["estimate_seconds"] = None
        self.assertIsNone(validate_plan(plan, example_spec()))

    def test_review_required_must_be_bool(self):
        plan = example_plan()
        get_task(plan, "T2")["review_required"] = "yes"
        self.assertPlanError(InvalidReference, plan)

    def test_max_parallel_must_be_positive_int(self):
        for bad in (0, True, "2"):
            plan = example_plan()
            plan["max_parallel"] = bad
            self.assertPlanError(InvalidReference, plan)

    def test_unknown_decision_reference(self):
        plan = example_plan()
        get_task(plan, "T1")["decision_ids"] = ["DEC-99"]
        error = self.assertPlanError(InvalidReference, plan)
        self.assertEqual(error.details["unknown"], ["DEC-99"])

    def test_plan_must_bind_the_confirmed_spec_version(self):
        plan = example_plan()
        plan["spec_ref"] = {"id": "SPEC-99", "version": 1}
        error = self.assertPlanError(InvalidReference, plan)
        self.assertEqual(error.details["actual"], "SPEC-99")

        plan = example_plan()
        plan["spec_ref"]["version"] = 2
        error = self.assertPlanError(InvalidReference, plan)
        self.assertEqual(error.details["current_version"], 1)

    def test_empty_task_list(self):
        plan = example_plan()
        plan["tasks"] = []
        self.assertPlanError(InvalidReference, plan)

    def test_missing_spec_skips_binding_checks(self):
        self.assertIsNone(validate_plan(example_plan(), None))


class V1RegressionTest(unittest.TestCase):
    def test_v1_validate_still_accepts_the_mock_plan(self):
        spec = {"id": "SPEC-01", "version": 1, "markdown": "# 需求"}
        validate(planner.decompose(spec))

    def test_v1_validate_keeps_its_message(self):
        plan = planner.decompose({"id": "SPEC-01", "version": 1, "markdown": "# 需求"})
        plan["tasks"][0]["depends_on"] = ["T4"]
        with self.assertRaisesRegex(ValueError, "依赖成环"):
            validate(plan)


if __name__ == "__main__":
    unittest.main()