from contextlib import closing
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from se_agent.agent_client import AgentError, ChatAgent


class ChatAgentTest(unittest.TestCase):
    def test_deepseek_synthesis_requires_complete_stream(self):
        agent = ChatAgent("https://api.deepseek.com", "deepseek-flash")
        parts = ['{"markdown":', '"摘要"}']
        chunks = [
            {"choices": [{"delta": {"content": part}, "finish_reason": None}]}
            for part in parts
        ]
        chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        wire = "".join("data: "+json.dumps(chunk)+"\n\n" for chunk in chunks).encode()
        with patch("se_agent.agent_client.urlopen", return_value=BytesIO(wire+b"data: [DONE]\n")) as transport:
            self.assertEqual(agent("synthesize", {}), {"markdown": "摘要"})
            self.assertTrue(json.loads(transport.call_args.args[0].data)["stream"])
            self.assertEqual(transport.call_args.kwargs["timeout"], 180)
        with patch("se_agent.agent_client.sleep"), patch(
                "se_agent.agent_client.urlopen", side_effect=lambda *a, **k: BytesIO(wire)) as transport:
            with self.assertRaisesRegex(AgentError, "响应在完成前中断"):
                agent("synthesize", {})
            self.assertEqual(transport.call_count, 3)

    def test_each_stage_sends_its_own_context_and_parses_json(self):
        agent = ChatAgent("http://127.0.0.1:11434/v1", "test-model")
        calls = []

        def transport(request, timeout):
            calls.append((request.full_url, json.loads(request.data)))
            response = {"choices": [{"message": {"content": json.dumps({"question": "缺少啥？"})}}]}
            return closing(BytesIO(json.dumps(response).encode()))

        with patch("se_agent.agent_client.urlopen", side_effect=transport):
            actual = agent("challenge", {"group_id": "C", "group_prompt": "核对迁移成本"})
        self.assertEqual(actual, "缺少啥？")
        url, body = calls[0]
        self.assertTrue(url.endswith("/v1/chat/completions"))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(json.loads(body["messages"][1]["content"]),
                         {"group_id": "C", "group_prompt": "核对迁移成本"})
