from copy import deepcopy
import json
from pathlib import Path
import unittest

from se_agent import synthesis as s

ROOT = Path(__file__).resolve().parent.parent


def handoff(name="conflict"):
    return json.loads((ROOT / "examples" / "review-synthesis" / (name + ".json")).read_text(encoding="utf-8"))


class SynthesisTest(unittest.TestCase):
    def setUp(self):
        self.data = handoff()
        self.req, self.drafts = self.data["requirement"], self.data["drafts"]
        self.evaluations = [s.evaluate_draft(self.req, d) for d in self.drafts]

    def proposal(self, selection="merge", **kwargs):
        return s.propose_spec(self.req, self.drafts, self.evaluations, selection, **kwargs)

    def test_a_b_merge_and_requirement_coverage(self):
        for selection in ("A", "B", "merge"):
            with self.subTest(selection=selection):
                result = self.proposal(selection)
                covered = {r for d in result["decisions"] for r in d["requirement_ids"]}
                covered.update(r for o in result["open_items"] for r in o["affected_requirement_ids"])
                self.assertEqual(covered, {"REQ-01", "REQ-02"})
                self.assertIn(self.req["markdown"], result["markdown"])
                self.assertNotIn("confirmed_by", result)
                for d in result["decisions"]:
                    self.assertTrue(set(d["draft_ids"]) <= ({"A", "B"} if selection == "merge" else {selection}))

    def test_extra_cancellation_remains_an_open_decision(self):
        result = self.proposal()
        item = next(o for o in result["open_items"] if "自动取消" in o["question"])
        self.assertTrue(item["blocking"])
        self.assertEqual(item["affected_requirement_ids"], ["REQ-02"])
        self.assertIsNone(item["resolution"])
        self.assertTrue(all("自动取消" not in d["choice"] for d in result["decisions"]))

    def test_authorized_cancellation_is_not_misreported(self):
        self.req["items"][1]["text"] += " 停用时自动取消受影响的已有预约。"
        self.req["markdown"] += "\n停用时自动取消受影响的已有预约。"
        result = self.proposal()
        self.assertFalse(any("自动取消" in o["question"] for o in result["open_items"]))

    def test_actual_cly_handoff_and_explicit_question(self):
        data = handoff("cly-handoff")
        evaluations = [s.evaluate_draft(data["requirement"], d) for d in data["drafts"]]
        result = s.propose_spec(data["requirement"], data["drafts"], evaluations, "merge")
        self.assertTrue(any("已有预约" in o["question"] and o["resolution"] is None for o in result["open_items"]))

    def test_recorded_browser_exports_are_valid_handoffs(self):
        for name in ("confirmed-human", "confirmed-cly"):
            with self.subTest(name=name):
                data = handoff(name)
                s.validate_handoff(data["requirement"], data["drafts"])
                s.validate_proposal(data["requirement"], data["drafts"], data["specification"])
                self.assertEqual(data["specification"]["status"], "confirmed")
                self.assertEqual(data["specification"]["confirmed_by"], "lead-demo")
                self.assertTrue(data["specification"]["confirmed_at"])
        human = handoff("confirmed-human")["specification"]
        self.assertTrue(any("待改期" in d["choice"] for d in human["decisions"]))
        self.assertIsNotNone(human["open_items"][0]["resolution"])
        self.assertIsNone(handoff("confirmed-cly")["specification"]["open_items"][0]["resolution"])

    def test_single_draft_context_cannot_mutate_inputs_or_reuse_outputs(self):
        before = deepcopy(self.data)
        output = deepcopy(self.evaluations[0])
        def agent(stage, context):
            self.assertEqual(stage, "evaluate")
            self.assertEqual(set(context), {"requirement", "draft"})
            self.assertEqual(context["draft"]["id"], "A")
            context["requirement"]["markdown"] = "mutated"
            context["draft"]["claims"].clear()
            return output
        result = s.evaluate_draft(self.req, self.drafts[0], agent=agent)
        output["criteria"].clear()
        self.assertEqual(len(result["criteria"]), 3)
        self.assertEqual(self.data, before)

    def test_synthesis_callback_is_isolated_and_validated(self):
        before = deepcopy((self.data, self.evaluations))
        expected = self.proposal()
        def agent(stage, context):
            self.assertEqual(stage, "synthesize")
            self.assertEqual(context["selection"], "merge")
            context["drafts"].clear()
            context["evaluations"].clear()
            return expected
        actual = self.proposal(agent=agent)
        expected["decisions"].clear()
        self.assertTrue(actual["decisions"])
        self.assertEqual((self.data, self.evaluations), before)

    def test_rejects_invalid_evaluation_output(self):
        changes = [lambda e: e["criteria"][0].update(score=True),
                   lambda e: e["criteria"][0].update(score=6),
                   lambda e: e["criteria"][0].update(score=2.5),
                   lambda e: e["criteria"][0].update(reason=" "),
                   lambda e: e["criteria"][0].update(claim_ids=["B-01"]),
                   lambda e: e["criteria"][1].update(criterion="value"),
                   lambda e: e.update(draft_id="B")]
        for change in changes:
            with self.subTest(change=change):
                result = deepcopy(self.evaluations[0])
                change(result)
                with self.assertRaises(s.SynthesisError):
                    s.evaluate_draft(self.req, self.drafts[0], agent=lambda *_: result)

    def test_rejects_missing_or_mismatched_handoff(self):
        changes = [lambda d: d["drafts"].pop(),
                   lambda d: d["drafts"][1].update(id="A"),
                   lambda d: d["drafts"][1]["requirement_ref"].update(version=2),
                   lambda d: d["drafts"][1]["claims"][0].update(requirement_ids=["REQ-404"]),
                   lambda d: d["requirement"].pop("version"),
                   lambda d: d["requirement"]["items"][0]["source"].update(end_line=99),
                   lambda d: d["drafts"][0]["claims"].append(deepcopy(d["drafts"][0]["claims"][0]))]
        for change in changes:
            with self.subTest(change=change):
                data = deepcopy(self.data)
                change(data)
                with self.assertRaises(s.SynthesisError):
                    s.validate_handoff(data["requirement"], data["drafts"])

    def test_invalid_selection_or_missing_evaluation(self):
        with self.assertRaises(s.SynthesisError):
            self.proposal("C")
        with self.assertRaises(s.SynthesisError):
            s.propose_spec(self.req, self.drafts, self.evaluations[:1], "merge")

    def test_custom_output_cannot_omit_requirements_or_forge_sources(self):
        for mutation in (lambda p: p.update(decisions=[], open_items=[]),
                         lambda p: p["decisions"][0].update(draft_ids=["C"]),
                         lambda p: p["open_items"][0].pop("resolution"),
                         lambda p: p["open_items"][0].update(blocking="yes")):
            result = self.proposal()
            mutation(result)
            with self.assertRaises(s.SynthesisError):
                self.proposal(agent=lambda *_: result)


if __name__ == "__main__":
    unittest.main()
