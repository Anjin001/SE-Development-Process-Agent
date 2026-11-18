"""Verify a worker's commit against the plan baseline and its write scope.

docs/接口契约-v2.md §5 asks the orchestration side to check that a reported
commit is readable, descends from the attempt's ``checkout_commit``, already
contains every required upstream SHA, and only touches the task's
``write_paths``. Failures surface as OUTPUT_INVALID so the attempt can be
retried with a structured reason.
"""
import subprocess

from .contracts import OutputInvalid


class CommitVerifier:
    def __init__(self, repo):
        self.repo = str(repo)

    def _git(self, *args):
        result = subprocess.run(["git", "-C", self.repo, *args],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise OutputInvalid("Git 命令失败", command=" ".join(args),
                                stderr=result.stderr.strip())
        return result.stdout

    def exists(self, commit_sha):
        if not commit_sha or len(commit_sha) < 7:
            return False
        result = subprocess.run(
            ["git", "-C", self.repo, "cat-file", "-e", f"{commit_sha}^{{commit}}"],
            capture_output=True)
        return result.returncode == 0

    def is_ancestor(self, ancestor, descendant):
        result = subprocess.run(
            ["git", "-C", self.repo, "merge-base", "--is-ancestor",
             ancestor, descendant], capture_output=True)
        return result.returncode == 0

    def changed_paths(self, commit_sha, base_commit=None):
        if base_commit:
            output = self._git("diff", "--name-only", f"{base_commit}..{commit_sha}")
        else:
            output = self._git("show", "--pretty=format:", "--name-only", commit_sha)
        return [line.strip() for line in output.splitlines() if line.strip()]

    def verify(self, commit_sha, checkout_commit=None, required_shas=(),
               allowed_paths=()):
        if not self.exists(commit_sha):
            raise OutputInvalid("产物提交不可读", field="commit_sha",
                                commit_sha=commit_sha)
        if checkout_commit and not self.is_ancestor(checkout_commit, commit_sha):
            raise OutputInvalid("产物提交不是本次基线的后代", field="commit_sha",
                                checkout_commit=checkout_commit,
                                commit_sha=commit_sha)
        missing = [sha for sha in required_shas
                   if sha and not self.is_ancestor(sha, commit_sha)]
        if missing:
            raise OutputInvalid("上游提交未纳入本次产物", field="inputs",
                                missing=missing)
        if allowed_paths:
            outside = [path for path in self.changed_paths(commit_sha, checkout_commit)
                       if not any(path == allowed or path.startswith(allowed.rstrip("/") + "/")
                                  for allowed in allowed_paths)]
            if outside:
                raise OutputInvalid("变更超出允许的写入范围", field="changed_paths",
                                    paths=outside)
        return None