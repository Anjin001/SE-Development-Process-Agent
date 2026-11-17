from contextlib import closing
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from se_agent.agent_client import ChatAgent


class ChatAgentTest(unittest.TestCase):
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
