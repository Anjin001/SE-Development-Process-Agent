"""v2 Worker 验收：模拟链路形状、缺输入、写入范围、真实 worktree 纳入上游与冲突。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from se_agent.api_contract import parse_endpoints
from se_agent.model_router import ModelRouter, load_config
from io import BytesIO
from unittest.mock import patch
from se_agent.providers import LLMResult, OpenAICompatibleProvider
from se_agent.runner import execute_task
from se_agent.worker import Worker

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = json.loads((ROOT / "examples" / "v2" / "scenario.json").read_text(encoding="utf-8"))
CANDIDATE_FIELDS = ["changed_paths", "commit_sha", "content", "kind", "output_id"]


def task_by_id(task_id):
    for task in SCENARIO["plan_response"]["plan"]["tasks"]:
        if task["id"] == task_id:
            return json.loads(json.dumps(task))
    raise KeyError(task_id)


def make_request(task, **overrides):
    request = {"project_id": "PROJECT-01", "run_id": "RUN-01",
               "attempt_id": f'{task["id"]}.1', "task": task,
               "spec_ref": {"id": "SPEC-01", "version": 1},
               "spec_context": {"requirements": [], "decisions": [], "open_items": []},
               "base_commit": None, "workspace_id": "WS-01",
               "inputs": [], "supplied_inputs": {}}
    request.update(overrides)
    return request


def open_items():
    return SCENARIO["project_snapshot"]["specification"]["open_items"]


class MockChainTest(unittest.TestCase):
    def test_scenario_request_returns_candidate_artifact(self):
        request = json.loads(json.dumps(SCENARIO["next_worker_request"]))
        result = execute_task(request, Worker(mode="mock"))

        self.assertEqual(sorted(result), ["artifacts", "attempt_id", "checkout_commit",
                                          "ended_at", "error", "model", "provider", "started_at",
                                          "status", "usage"])
        self.assertEqual(result["attempt_id"], "T4.1")
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["error"])
        self.assertEqual(result["provider"], "mock")
        self.assertIsNone(result["model"])
        self.assertIsNone(result["usage"])
        self.assertIsNone(result["checkout_commit"])
        self.assertRegex(result["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

        self.assertEqual(len(result["artifacts"]), 1)
        artifact = result["artifacts"][0]
        self.assertEqual(sorted(artifact), CANDIDATE_FIELDS)
        self.assertEqual(artifact["output_id"], "OUT-01")
        self.assertEqual(artifact["kind"], "git_commit")
        self.assertEqual(artifact["changed_paths"], ["app/integration.py"])
        self.assertIsNone(artifact["content"])
        self.assertRegex(artifact["commit_sha"], r"^[0-9a-f]{40}$")

    def test_text_output_carries_content(self):
        result = execute_task(make_request(task_by_id("T3")), Worker(mode="mock"))
        artifact = result["artifacts"][0]
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(artifact["kind"], "text")
        self.assertEqual(artifact["changed_paths"], [])
        self.assertIsNone(artifact["commit_sha"])
        self.assertIn("整理使用说明", artifact["content"])

    def test_missing_input_then_supplied(self):
        request = make_request(task_by_id("T2"), spec_context={
            "requirements": [], "decisions": [], "open_items": open_items()})
        result = execute_task(request, Worker(mode="mock"))

        self.assertEqual(result["status"], "input_required")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["error"]["code"], "MISSING_INPUT")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(result["error"]["details"]["item_ids"], ["OPEN-01"])
        self.assertIn("OPEN-01", result["error"]["details"]["questions"])

        request["supplied_inputs"] = {"OPEN-01": "LAB-DEMO-07"}
        self.assertEqual(execute_task(request, Worker(mode="mock"))["status"], "succeeded")

    def test_output_outside_write_paths_is_rejected(self):
        task = task_by_id("T1")
        task["outputs"] = [{"id": "OUT-01", "kind": "git_commit", "path": "app/other.py"}]
        result = execute_task(make_request(task), Worker(mode="mock"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "OUTPUT_INVALID")
        self.assertEqual(result["error"]["details"]["paths"], ["app/other.py"])


class RouterTest(unittest.TestCase):
    def test_complex_task_runs_two_stages(self):
        router = ModelRouter(load_config())
        steps = router.plan({"role": "integrate"})
        self.assertEqual([s["stage"] for s in steps], ["boundary", "implement"])
        self.assertEqual([s["tier"] for s in steps], ["high_capability", "low_cost"])

    def test_simple_task_uses_low_cost(self):
        router = ModelRouter(load_config())
        steps = router.plan({"role": "docs"})
        self.assertEqual([s["stage"] for s in steps], ["implement"])
        self.assertEqual(steps[0]["tier"], "low_cost")


class StubExecutor:
    """替真实模型执行器，按步记录调用并返回固定内容。"""

    name = "stub"

    def __init__(self, text="integrated\n"):
        self.text = text
        self.steps = []

    def run_step(self, request, task, step, provider):
        self.steps.append(step["stage"])
        return LLMResult(self.text, 10, 5)


def git(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip()


def is_ancestor(cwd, ancestor, descendant):
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant],
                          cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode == 0


class ContractOwnershipTest(unittest.TestCase):
    def test_route_validation_distinguishes_wrong_owner_and_normalized_conflict(self):
        with self.assertRaisesRegex(ValueError, "不属于本组路由前缀 /api/reactions"):
            parse_endpoints('{"endpoints":[{"method":"POST","path":"/api/notes/like","handler":"like"}]}',
                            set(),prefix="/api/reactions")
        with self.assertRaisesRegex(ValueError, "路由重复"):
            parse_endpoints('{"endpoints":[{"method":"GET","path":"/api/notes/{note_id}","handler":"read"}]}',
                            {("GET","/api/notes/{}")},prefix="/api/notes")


class RealWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "test")
        (self.root / "app").mkdir()
        (self.root / "app" / "status.py").write_text("STATUS = {}\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def upstream(self, name, text):
        git(self.root, "checkout", "-b", name, self.base)
        (self.root / "app" / "reservations.py").write_text(text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", name)
        sha = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "main")
        return sha

    def request(self, inputs, task=None):
        task = task or {"id": "T4", "title": "集成预约规则", "phase": "integration",
                        "requirement_ids": ["REQ-01"], "decision_ids": ["DEC-01"],
                        "open_item_ids": [], "depends_on": ["T2", "T3"], "role": "develop",
                        "write_paths": ["app/integration.py"],
                        "outputs": [{"id": "OUT-01", "kind": "git_commit",
                                     "path": "app/integration.py"}],
                        "estimate_seconds": 300, "review_required": False,
                        "completion_condition": "集成预约规则提交后可按输出路径读取。"}
        return make_request(task, attempt_id=f'{task["id"]}.1', base_commit=self.base,
                            inputs=inputs)

    def artifact(self, task_id, sha):
        return {"id": f"ART-{task_id}", "version": 1, "task_id": task_id,
                "attempt_id": f"{task_id}.1", "output_id": "OUT-01", "kind": "git_commit",
                "origin": "agent", "status": "approved", "based_on_version": None,
                "commit_sha": sha, "changed_paths": ["app/reservations.py"], "content": None}

    def worker(self, executor=None, router=None):
        return Worker(mode="real", repo_root=str(self.root),
                      worktree_root=str(self.root / ".worktrees"),
                      executor=executor or StubExecutor(), router=router)

    def test_includes_approved_upstream_and_reports_sha(self):
        upstream = self.upstream("upstream", "RULES = 1\n")
        executor = StubExecutor()
        result = execute_task(self.request([self.artifact("T2", upstream)]),
                              self.worker(executor))

        self.assertEqual(result["status"], "succeeded", result["error"])
        self.assertEqual(result["checkout_commit"], upstream)
        self.assertEqual(result["provider"], "openai_compatible")
        self.assertEqual(result["model"], "gpt-4o-mini")
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 5})

        artifact = result["artifacts"][0]
        self.assertEqual(sorted(artifact), CANDIDATE_FIELDS)
        self.assertEqual(artifact["changed_paths"], ["app/integration.py"])
        self.assertNotEqual(artifact["commit_sha"], upstream)
        self.assertTrue(is_ancestor(self.root, upstream, artifact["commit_sha"]))
        git(self.root, "cat-file", "-e", f'{artifact["commit_sha"]}:app/reservations.py')

    def test_deepseek_file_generation_reserves_body_and_reports_truncation(self):
        requests = []
        def respond(req, **kwargs):
            body = json.loads(req.data)
            requests.append(body)
            return BytesIO(json.dumps({"choices":[{"finish_reason":"length","message":{
                "reasoning_content":"not file content","content":""}}],
                "usage":{"prompt_tokens":260,"completion_tokens":body["max_tokens"]}}).encode())

        provider = OpenAICompatibleProvider("https://api.deepseek.com", api_key="fixture")
        router = ModelRouter({"tiers":{"low_cost":{"provider":"openai_compatible","model":"deepseek-flash",
                                  "max_output_tokens":4096}},
                              "routing":{"default_tier":"low_cost"}})
        worker = Worker(mode="real", repo_root=str(self.root),
                        worktree_root=str(self.root / ".worktrees"), provider=provider, router=router)
        with patch("se_agent.providers.urllib.request.urlopen", side_effect=respond):
            result = execute_task(self.request([]), worker)
        self.assertEqual(requests[0]["thinking"], {"type":"disabled"})
        self.assertEqual(result["error"]["details"]["path"],"app/integration.py")
        self.assertEqual([b["max_tokens"] for b in requests],[4096,8192])
        self.assertEqual(result["error"]["details"]["max_tokens"],8192)
        self.assertEqual(result["usage"],{"input_tokens":520,"output_tokens":12288})
        self.assertEqual(result["artifacts"],[])
        self.assertNotIn("not file content",result["error"]["message"])

    def test_blank_output_is_rejected_before_writing_or_committing(self):
        result = execute_task(self.request([]), self.worker(StubExecutor("   \n")))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["error"]["code"], "OUTPUT_INVALID")
        self.assertIn("app/integration.py",result["error"]["message"])
        self.assertEqual(git(self.root,"rev-parse","attempt/RUN-01/T4.1"),self.base)
        self.assertFalse((self.root/".worktrees"/"RUN-01-T4.1"/"app"/"integration.py").exists())

    def test_merge_conflict_reports_paths(self):
        first = self.upstream("upstream-a", "RULES = 'a'\n")
        second = self.upstream("upstream-b", "RULES = 'b'\n")
        result = execute_task(self.request([self.artifact("T2", first),
                                            self.artifact("T3", second)]),
                              self.worker())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["error"]["code"], "MERGE_CONFLICT")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(result["error"]["details"]["paths"], ["app/reservations.py"])
        self.assertIsNone(result["checkout_commit"])

    def test_cost_cap_stops_attempt(self):
        config = load_config()
        config["routing"]["max_cost_usd_per_attempt"] = 0.000001
        result = execute_task(self.request([]), self.worker(router=ModelRouter(config)))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "EXECUTION_FAILED")
        self.assertEqual(result["error"]["details"]["limit_usd"], 0.000001)


if __name__ == "__main__":
    unittest.main()
