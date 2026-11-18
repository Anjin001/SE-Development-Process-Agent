"""Git 工作区适配。

真实执行时每个尝试在同一个对象库下开一个独立 worktree：从绑定基线建本次
尝试的分支，再按 `task_id, output_id` 升序把已批准的直接上游提交合进来。
上游提交自身已经包含它执行时纳入的更早依赖，所以只合直接上游即可。
合并冲突按路径回报，工作区不可读时同样回报错误。
"""
import re
import subprocess
from pathlib import Path


class WorkspaceError(RuntimeError):
    """工作区准备、改文件或提交失败。Worker 转成 EXECUTION_FAILED。"""


class MergeConflict(WorkspaceError):
    """上游提交无法合并，携带冲突路径。"""

    def __init__(self, paths):
        super().__init__("上游提交无法合并：" + "、".join(sorted(paths)))
        self.paths = sorted(paths)


class WorktreeAdapter:
    """在一个仓库上为单次尝试开独立 worktree。"""

    def __init__(self, repo_root, worktree_root=None):
        self.repo_root = Path(repo_root).resolve()
        self.worktree_root = Path(worktree_root) if worktree_root else self.repo_root / ".se_agent_worktrees"
        self.branch = None
        self.path = None

    def _run_git(self, *args, cwd=None):
        # Git can emit UTF-8 output regardless of the host's default code page.
        return subprocess.run(["git", *args], cwd=str(cwd or self.repo_root),
                              capture_output=True, encoding="utf-8", errors="replace")

    def git(self, *args, cwd=None, check=True):
        proc = self._run_git(*args, cwd=cwd)
        if check and proc.returncode:
            raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} 执行失败")
        return proc.stdout.strip()

    def prepare(self, run_id, attempt_id, base_commit):
        """从基线创建本次尝试的分支与工作目录，返回工作目录。"""
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
               or value in (".", "..") for value in (run_id, attempt_id)):
            raise WorkspaceError("运行和尝试编号格式无效")
        if not base_commit:
            raise WorkspaceError("真实执行需要 base_commit")
        if not (self.repo_root / ".git").exists():
            raise WorkspaceError(f"{self.repo_root} 不是 Git 仓库")
        self.branch = f"attempt/{run_id}/{attempt_id}"
        self.path = self.worktree_root / f"{run_id}-{attempt_id}"
        self._discard()
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-B", self.branch, str(self.path), base_commit)
        return self.path

    def include(self, commits):
        """按顺序纳入上游提交，返回纳入后的完整 SHA 作为本次起点。

        只有一个上游时是快进，起点即该上游提交；多个上游时形成显式合并提交。
        """
        self.ensure_readable(commits)
        for sha in commits:
            proc = self._run_git("merge", "--no-edit", sha, cwd=self.path)
            if proc.returncode:
                paths = self.conflicted_paths() or [sha]
                self.git("merge", "--abort", cwd=self.path, check=False)
                raise MergeConflict(paths)
        return self.head()

    def ensure_readable(self, commits):
        """服务端核验可达性前的第一道检查：批准提交必须能读到。"""
        for sha in commits:
            proc = self._run_git("cat-file", "-e", f"{sha}^{{commit}}", cwd=self.path)
            if proc.returncode:
                raise WorkspaceError(f"无法读取批准提交 {sha}")

    def conflicted_paths(self):
        out = self.git("diff", "--name-only", "--diff-filter=U", cwd=self.path, check=False)
        return [line for line in out.splitlines() if line.strip()]

    def head(self):
        return self.git("rev-parse", "HEAD", cwd=self.path)

    def commit_paths(self, message, paths):
        """暂存指定路径并提交，返回提交号；没有改动时停在当前 HEAD。"""
        self.git("add", "--", *paths, cwd=self.path)
        self.git("-c", "user.name=se-agent", "-c", "user.email=se-agent@local",
                 "commit", "-m", message, cwd=self.path, check=False)
        return self.head()

    def diff_paths(self, base):
        """从起点到当前 HEAD 的改动路径，用于校验写入范围。"""
        out = self.git("diff", "--name-only", base, "HEAD", cwd=self.path)
        return sorted(line for line in out.splitlines() if line.strip())

    def cleanup(self):
        """释放工作目录。提交保留在分支上，产物在运行结束后仍可读取。"""
        if self.path and self.path.exists():
            self.git("worktree", "remove", "--force", str(self.path), check=False)
        self.git("worktree", "prune", check=False)

    def _discard(self):
        """清掉同名尝试留下的注册，重试时才能再次 add。"""
        self.git("worktree", "prune")
        if self.path and self.path.exists():
            self.git("worktree", "remove", "--force", str(self.path), check=False)
        self.git("branch", "-D", self.branch, check=False)
