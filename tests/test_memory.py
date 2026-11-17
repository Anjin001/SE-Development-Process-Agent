"""Experience cards: publishing, alias-aware retrieval, access and usage."""
import unittest
from datetime import datetime, timezone

from se_agent.contracts import InvalidReference
from se_agent.memory import Memory
from se_agent.store import Store

PROJECT = "PROJECT-01"
OTHER = "PROJECT-02"


def clock():
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return lambda: moment


def card(**overrides):
    base = {
        "domain": "校园资源预约",
        "symptom": "方案允许管理员停用座位，却漏写该座位已有预约时怎么处理",
        "trigger": "操作会让资源不可用，并且可能影响已有有效预约",
        "check_stage": "交叉评审一次，整合后复查一次",
        "correction": "检查是否阻止新预约、如何处理受影响预约、如何告知预约人",
        "pass_condition": "草案写明受影响预约范围与处理方式",
        "scope": "停用时段与已有预约重叠时适用",
        "exceptions": "停用时段与所有预约均不重叠时不触发",
        "objects": ["可预约资源"],
        "operations": ["资源转为不可用"],
        "states": ["存在有效预约"],
        "keywords": ["已确认预约怎么办", "周五送修"],
        "evidence_state": "walkthrough",
        "publish_state": "published",
    }
    base.update(overrides)
    return base


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:", now=clock())
        self.memory = Memory(self.store)
        self.store.create_project(PROJECT)
        self.store.create_project(OTHER)
        for tag_type, alias, tag in (
                ("object", "设备", "可预约资源"),
                ("object", "座位", "可预约资源"),
                ("operation", "停用", "资源转为不可用"),
                ("operation", "暂停开放", "资源转为不可用"),
                ("operation", "下架", "资源转为不可用")):
            self.memory.alias("校园资源预约", tag_type, alias, tag)

    def tearDown(self):
        self.store.close()

    def publish(self, origin=PROJECT, **overrides):
        return self.memory.publish_experience(
            card(**overrides), origin_project=origin,
            sources=[{"id": "CASE-01", "version": 1, "project": origin}])


class RetrievalTest(MemoryTestCase):
    def test_aliases_and_all_three_dimensions_hit(self):
        self.publish()
        hits = self.memory.search(PROJECT, objects=["设备"], operations=["停用"],
                                  states=["存在有效预约"], domain="校园资源预约")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["trigger_matched"])
        types = {item["type"] for item in hits[0]["matched"]}
        self.assertEqual(types, {"object", "operation", "state"})

    def test_same_object_with_a_different_operation_is_not_a_full_trigger(self):
        self.publish()
        hits = self.memory.search(PROJECT, objects=["设备"],
                                  operations=["修改设备名称"], domain="校园资源预约")
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0]["trigger_matched"])

    def test_keyword_text_finds_the_card(self):
        self.publish()
        hits = self.memory.search(PROJECT, text="已经约了周五, 周五送修怎么办")
        self.assertEqual([hit["experience_id"] for hit in hits], ["EXP-001"])
        self.assertEqual(hits[0]["matched"][0]["type"], "keyword")

    def test_nothing_matches_returns_empty(self):
        self.publish()
        self.assertEqual(self.memory.search(OTHER, objects=["会议室"],
                                            operations=["预订"]), [])

    def test_stronger_evidence_ranks_first(self):
        first = self.publish(evidence_state="pending")
        self.memory.publish_experience(
            card(experience_id=first["experience_id"], symptom="另一张", evidence_state="production"),
            origin_project=PROJECT, sources=[{"id": "CASE-02", "project": PROJECT}])
        hits = self.memory.search(PROJECT, objects=["设备"], operations=["停用"],
                                  states=["存在有效预约"], domain="校园资源预约")
        self.assertEqual(hits[0]["version"], 2)
        self.assertEqual(hits[0]["evidence_state"], "production")

    def test_limit_caps_the_result(self):
        self.publish()
        self.publish(origin=PROJECT)
        hits = self.memory.search(PROJECT, objects=["设备"], limit=1)
        self.assertEqual(len(hits), 1)


class AccessTest(MemoryTestCase):
    def test_candidate_stays_inside_its_origin_project(self):
        self.publish(origin=OTHER, publish_state="candidate")
        self.assertEqual(self.memory.search(PROJECT, objects=["设备"]), [])
        self.assertEqual(len(self.memory.search(OTHER, objects=["设备"])), 1)

    def test_published_card_travels_between_projects(self):
        first = self.publish(origin=OTHER, publish_state="candidate")
        self.memory.publish_experience(
            card(experience_id=first["experience_id"], publish_state="published"),
            origin_project=OTHER)
        hits = self.memory.search(PROJECT, objects=["设备"], operations=["停用"],
                                  states=["存在有效预约"], domain="校园资源预约")
        self.assertEqual(hits[0]["version"], 2)

    def test_ideation_only_sees_the_published_baseline(self):
        self.publish(origin=PROJECT, publish_state="candidate")
        self.assertEqual(self.memory.search(PROJECT, objects=["设备"], stage="race"), [])
        self.assertEqual(len(self.memory.search(PROJECT, objects=["设备"], stage="execution")), 1)

    def test_retired_version_disappears_from_search(self):
        first = self.publish()
        self.memory.publish_experience(
            card(experience_id=first["experience_id"], publish_state="retired"),
            origin_project=PROJECT)
        self.assertEqual(self.memory.search(PROJECT, objects=["设备"]), [])
        self.assertEqual(len(self.memory.history("EXP-001")), 2)

    def test_history_keeps_every_version_readable(self):
        first = self.publish()
        self.memory.publish_experience(card(experience_id=first["experience_id"],
                                            correction="改了纠正动作"),
                                       origin_project=PROJECT)
        history = self.memory.history("EXP-001")
        self.assertEqual([item["version"] for item in history], [1, 2])
        self.assertEqual(history[1]["correction"], "改了纠正动作")

    def test_missing_required_field_is_rejected(self):
        with self.assertRaises(InvalidReference):
            self.memory.publish_experience(card(correction="  "))


class UsageTest(MemoryTestCase):
    def test_usage_is_recorded_and_updateable(self):
        self.publish()
        self.memory.record_usage("RUN-01", "AGENT-01", "EXP-001", 1, "adopted",
                                 reason="命中停用场景", expected="补齐预约处理")
        rows = self.memory.usages("EXP-001")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "adopted")
        self.memory.record_usage("RUN-01", "AGENT-01", "EXP-001", 1, "declined",
                                 reason="本次停用不影响已有预约")
        rows = self.memory.usages("EXP-001", 1)
        self.assertEqual(rows[0]["decision"], "declined")

    def test_bad_decision_is_rejected(self):
        with self.assertRaises(InvalidReference):
            self.memory.record_usage("RUN-01", "AGENT-01", "EXP-001", 1, "maybe")


if __name__ == "__main__":
    unittest.main()