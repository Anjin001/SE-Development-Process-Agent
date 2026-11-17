"""Commit verification against a real repository: reachability, ancestry, scope."""
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

from se_agent.contracts import OutputInvalid
from se_agent.gitcheck import CommitVerifier
from se_agent.store import Store

FIXTURE = Path(__file__).with_name("_git_fixture")


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class Repo:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        git(self.path, "init", "-q")
        git(self.path, "config", "user.name", "tester")
        git(self.path, "config", "user.email", "tester@example.com")

    def commit(self, name, content, message):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git(self.path, "add", name)
        git(self.path, "commit", "-q", "-m", message)
        return git(self.path, "rev-parse", "HEAD")


class GitCase(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(FIXTURE, ignore_errors=True)
        self.repo = Repo(FIXTURE)
        self.c1 = self.repo.commit("app/status.py", "v1\n", "add status")
        self.c2 = self.repo.commit("app/status.py", "v2\n", "change status")
        self.c3 = self.repo.commit("docs/notes.md", "notes\n", "add notes")
        self.verifier = CommitVerifier(FIXTURE)

    def tearDown(self):
        shutil.rmtree(FIXTURE, ignore_errors=True)


class VerifierTest(GitCase):
    def test_accepts_a_commit_inside_the_write_scope(self):
        self.assertIsNone(self.verifier.verify(self.c2,
                                               allowed_paths=["app/status.py"]))

    def test_rejects_changes_outside_the_write_scope(self):
        with self.assertRaises(OutputInvalid) as caught:
            self.verifier.verify(self.c3, allowed_paths=["app/status.py"])
        self.assertEqual(caught.exception.code, "OUTPUT_INVALID")
        self.assertEqual(caught.exception.details["paths"], ["docs/notes.md"])

    def test_directory_scope_covers_its_children(self):
        self.assertIsNone(self.verifier.verify(self.c3, allowed_paths=["docs"]))

    def test_rejects_an_unreadable_commit(self):
        with self.assertRaises(OutputInvalid) as caught:
            self.verifier.verify("d" * 40)
        self.assertIn("不可读", caught.exception.message)

    def test_requires_the_commit_to_descend_from_checkout(self):
        with self.assertRaises(OutputInvalid):
            self.verifier.verify(self.c1, checkout_commit=self.c2)
        self.assertIsNone(self.verifier.verify(self.c2, checkout_commit=self.c1))

    def test_requires_upstream_shas_to_be_merged_in(self):
        self.assertIsNone(self.verifier.verify(self.c2, required_shas=[self.c1]))
        with self.assertRaises(OutputInvalid) as caught:
            self.verifier.verify(self.c1, required_shas=[self.c2])
        self.assertEqual(caught.exception.details["missing"], [self.c2])

    def test_changed_paths_use_the_checkout_as_base(self):
        self.assertEqual(self.verifier.changed_paths(self.c2, self.c1), ["app/status.py"])


def task(task_id, writes, depends_on=()):
    return {"id": task_id, "title": task_id, "phase": "development", "role": "develop",
            "depends_on": list(depends_on), "write_paths": [writes],
            "outputs": [{"id": "OUT-01", "kind": "git_commit", "path": writes}],
            "requirement_ids": [], "decision_ids": [], "open_item_ids": [],
            "estimate_seconds": 300, "review_required": False,
            "completion_condition": f"{task_id} 可读。"}


SPEC = {"id": "SPEC-01", "version": 1, "status": "confirmed", "markdown": "# 规格",
        "decisions": [], "open_items": []}


class StoreVerificationTest(GitCase):
    def start(self, plan_tasks, base):
        store = Store(":memory:", now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
                      verifier=self.verifier)
        store.create_project("P1")
        store.save_spec("P1", SPEC)
        plan = store.create_plan("P1", SPEC, base, 2, plan_tasks)
        run = store.create_run("P1", plan["id"], plan["version"], "auto")
        return store, run["id"]

    def submitted(self, store, run_id, commit):
        request = store.claim(run_id, "w1")[0]
        return store.submit(run_id, {"attempt_id": request["attempt_id"],
                                     "status": "succeeded",
                                     "artifacts": [{"output_id": "OUT-01",
                                                    "kind": "git_commit",
                                                    "commit_sha": commit}],
                                     "provider": "mock"}, "w1")

    def test_unreadable_commit_fails_the_attempt(self):
        store, run_id = self.start([task("T1", "app/status.py")], self.c1)
        try:
            snapshot = self.submitted(store, run_id, "d" * 40)
            self.assertEqual(snapshot["tasks"]["T1"]["state"], "failed")
            self.assertIn("OUTPUT_INVALID",
                          snapshot["tasks"]["T1"]["attempts"][0]["error"])
        finally:
            store.close()

    def test_in_scope_commit_is_accepted(self):
        store, run_id = self.start([task("T1", "app/status.py")], self.c1)
        try:
            snapshot = self.submitted(store, run_id, self.c2)
            self.assertEqual(snapshot["tasks"]["T1"]["state"], "done")
            self.assertEqual(snapshot["tasks"]["T1"]["artifacts"][0]["commit_sha"],
                             self.c2)
        finally:
            store.close()

    def test_out_of_scope_commit_is_rejected(self):
        store, run_id = self.start([task("T1", "app/status.py")], self.c1)
        try:
            snapshot = self.submitted(store, run_id, self.c3)
            self.assertEqual(snapshot["tasks"]["T1"]["state"], "failed")
            self.assertIn("docs/notes.md",
                          snapshot["tasks"]["T1"]["attempts"][0]["error"])
        finally:
            store.close()

    def test_downstream_must_have_merged_the_upstream_commit(self):
        store, run_id = self.start(
            [task("T1", "app/status.py"), task("T2", "docs/notes.md", ["T1"])], self.c1)
        try:
            self.submitted(store, run_id, self.c2)          # T1 delivers c2
            snapshot = self.submitted(store, run_id, self.c1)   # T2 reports an old commit
            self.assertEqual(snapshot["tasks"]["T2"]["state"], "failed")
            self.assertIn("上游提交未纳入",
                          snapshot["tasks"]["T2"]["attempts"][0]["error"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()