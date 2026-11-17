import unittest

from se_agent.intake import build_snapshot, extract_items, generate_draft, run_group


class IntakeRaceTest(unittest.TestCase):
    def setUp(self):
        self.markdown = "\n# 设备\n- 停用设备显示状态\n- 已有预约怎么办？\n\n```md\n# 示例，不是需求\n```"
        self.requirement = build_snapshot(self.markdown, "REQSET-01", 2)

    def test_extraction_preserves_source(self):
        items = self.requirement["items"]
        self.assertEqual(self.requirement["markdown"], self.markdown)
        self.assertEqual([item["source"]["start_line"] for item in items], [3, 4])
        self.assertEqual(items[0]["source"]["section"], "设备")
        with self.assertRaises(ValueError):
            extract_items("# 只有标题")

    def test_both_groups_reference_same_version(self):
        a = generate_draft(self.requirement, "A")
        b = generate_draft(self.requirement, "B")
        self.assertEqual(a["requirement_ref"], b["requirement_ref"])
        self.assertEqual(a["requirement_ref"]["version"], 2)
        self.assertNotEqual(a["title"], b["title"])
        self.assertIn("待确认", a["claims"][1]["text"])

    def test_context_isolation_and_invalid_reference(self):
        calls = []

        def agent(stage, context):
            calls.append((stage, context["group_id"], "ideas" in context))
            context["requirement"]["markdown"] = "modified"
            if stage == "idea":
                return {"proposal": "先支持列表", "risk": "已有预约待确认"}
            if stage == "challenge":
                return "核对需要处理的预约规则"
            return {"title": "草案", "summary": "说明",
                    "claims": [{"text": "没有依据", "requirement_ids": ["REQ-999"]}]}

        with self.assertRaisesRegex(ValueError, "未登记"):
            run_group(self.requirement, "A", agent=agent)
        self.assertEqual(calls, [("idea", "A", False), ("idea", "A", False),
                                 ("challenge", "A", True), ("draft", "A", True)])
        self.assertEqual(self.requirement["markdown"], self.markdown)

    def test_group_prompts_are_isolated_and_extra_groups_are_supported(self):
        seen = []

        def agent(stage, context):
            seen.append((context["group_id"], context["group_prompt"], stage))
            if stage == "idea":
                return {"proposal": "列出实施选项", "risk": "需要确认保留期限"}
            if stage == "challenge":
                return "确认成本和迁移风险"
            return {"title": context["group_prompt"], "summary": "说明",
                    "claims": [{"text": "保留设备状态", "requirement_ids": ["REQ-01"]}]}

        a = run_group(self.requirement, "A", prompt="优先控制成本", agent=agent)
        c = run_group(self.requirement, "C", prompt="优先保留迁移路径", agent=agent)
        stages = ["idea", "idea", "challenge", "draft"]
        self.assertEqual(seen, [("A", "优先控制成本", stage) for stage in stages]
                         + [("C", "优先保留迁移路径", stage) for stage in stages])
        self.assertEqual(c["draft"]["id"], "C")
        self.assertEqual(c["draft"]["requirement_ref"], a["draft"]["requirement_ref"])
        with self.assertRaisesRegex(ValueError, "提示词"):
            run_group(self.requirement, "C", prompt=" ")
        self.assertEqual(generate_draft(self.requirement, "C", prompt="比较迁移路径")["id"], "C")
