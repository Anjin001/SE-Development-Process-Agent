"""Progress and delivery services for the local workbench."""
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Lock
from time import monotonic
from uuid import uuid4

from .contracts import InvalidReference, InvalidState
from .gitcheck import CommitVerifier


class Progress:
    """Publish observable work milestones without saving model prompts or reasoning."""

    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def record(self, key, label, seconds=0):
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9.-]{1,100}", key):
            return
        with self.lock:
            now = monotonic()
            stages = self.entries.setdefault(key, [])
            stages.append((label, now, seconds))
            del stages[:-16]
            if len(self.entries) > 150:
                self.entries.pop(next(iter(self.entries)))

    def read(self, key):
        with self.lock:
            return [dict(label=label, elapsed_seconds=int(monotonic()-started), timeout_seconds=seconds)
                    for label, started, seconds in self.entries.get(key, [])]


class ProjectDelivery:
    def __init__(self, database):
        self.database = database
        database.db.execute("CREATE TABLE IF NOT EXISTS workspace_targets "
                            "(project_id TEXT PRIMARY KEY, path TEXT NOT NULL, repo TEXT UNIQUE)")

        database.db.execute("CREATE TABLE IF NOT EXISTS workspace_model_budgets "
                            "(project_id TEXT PRIMARY KEY, budget REAL NOT NULL)")

    def budget(self, project_id):
        row = self.database.db.execute(
            "SELECT budget FROM workspace_model_budgets WHERE project_id=?", (project_id,)).fetchone()
        return row["budget"] if row else None

    def set_budget(self, project_id, budget):
        with self.database.tx() as db:
            db.execute("INSERT INTO workspace_model_budgets VALUES(?,?) "
                       "ON CONFLICT(project_id) DO UPDATE SET budget=excluded.budget", (project_id, budget))

    def target(self, project_id):
        row = self.database.db.execute("SELECT * FROM workspace_targets WHERE project_id=?",
                                       (project_id,)).fetchone()
        return dict(path=row["path"], repo=row["repo"]) if row else dict(path="", repo=None)

    def verifier(self, project_id):
        repo = self.target(project_id)["repo"]
        return CommitVerifier(repo) if repo else self.database.verifier

    def configure(self, project_id, value, real):
        if (not isinstance(value, str) or not value.strip() or len(value) > 500
                or value.strip().startswith(("\\\\", "//"))):
            raise InvalidReference("请填写本机的完整文件夹路径。")
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise InvalidReference("请填绝对路径，例如 D:\\Projects\\MyApp。")
        path = path.resolve()
        source = Path(__file__).resolve().parents[1]
        if path == path.parent or path == source or path.is_relative_to(source):
            raise InvalidReference("请选择磁盘根目录和本工作台源码以外的文件夹。")
        old = self.target(project_id)
        repo = old["repo"]
        if real:
            name = str(path)
            if repo and Path(repo) != path:
                raise InvalidState("已有真实执行计划绑定到原仓库；可以另选导出目录。")
            other = self.database.db.execute("SELECT 1 FROM workspace_targets WHERE repo=? AND project_id<>?",
                                             (name, project_id)).fetchone()
            if other:
                raise InvalidReference("这个 Git 仓库已绑定另一个项目。")
            if not (path / ".git").exists():
                if path.exists() and (not path.is_dir() or any(
                        item.name != self.delivery_name(project_id) for item in path.iterdir())):
                    raise InvalidReference("用于真实执行的目录必须是 Git 仓库或空文件夹。")
                path.mkdir(parents=True, exist_ok=True)
                self._git(path, "init")
                self._git(path, "-c", "user.name=SE Agent", "-c", "user.email=se-agent@local",
                          "commit", "--allow-empty", "-m", "Initialize project workspace")
            elif not path.is_dir():
                raise InvalidReference("请选择文件夹。")
            verifier = CommitVerifier(path)
            verifier._git("rev-parse", "--verify", "HEAD")
            repo = name
        else:
            path.mkdir(parents=True, exist_ok=True)
        self.database.db.execute("INSERT INTO workspace_targets VALUES (?,?,?) "
                                 "ON CONFLICT(project_id) DO UPDATE SET path=excluded.path,repo=excluded.repo",
                                 (project_id, str(path), repo))
        return self.target(project_id)

    @staticmethod
    def _git(path, *args):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                                encoding="utf-8", errors="replace", timeout=15)
        if result.returncode:
            raise InvalidState("Git 初始化失败。请检查 Git 和文件夹权限。")

    @staticmethod
    def delivery_name(project_id):
        return "SE-Agent-交付-" + project_id[:8]

    def export(self, project):
        review, planning, run = (project[k] for k in ("review", "planning", "run"))
        target = self.target(review["id"])
        if not target["path"]:
            raise InvalidState("先选择一个保存文件夹。")
        parent = Path(target["path"])
        root = parent / self.delivery_name(review["id"])
        if root.is_symlink() or root.resolve().parent != parent:
            raise InvalidState("交付目录不能指向其他位置。")
        root.mkdir(exist_ok=True)
        files = {"requirement.md": review["requirement"]["markdown"]}
        if review["specification"]:
            files["specification.md"] = review["specification"]["markdown"]
        if planning:
            files["task.md"] = planning["task_markdown"]
            files["workflow.json"] = json.dumps(planning["plan"], ensure_ascii=False, indent=2)
        files["project-delivery.json"] = json.dumps(project, ensure_ascii=False, indent=2)
        if run:
            for task in run["tasks"].values():
                for artifact in task["artifacts"]:
                    if artifact["kind"] == "text" and artifact["status"] == "approved":
                        name = f"{task['task_id']}-{artifact['output_id']}-v{artifact['version']}.md"
                        if re.fullmatch(r"[\w.-]+", name):
                            files[name] = artifact["content"] or ""
        for name, content in files.items():
            tmp = root / (".tmp-" + uuid4().hex)
            try:
                with tmp.open("x", encoding="utf8") as out:
                    out.write(content)
                os.replace(tmp, root / name)
            finally:
                tmp.unlink(missing_ok=True)
        return dict(directory=str(root), files=list(files))


# Only internal choices with a fixed, reversible default may be resolved automatically.
_SAFE_DEFAULTS = (
    (r"数据库|持久化方案|数据存储方案",
     "原型使用本地 SQLite；保留替换存储的接口，不作上线或数据保留决定。"),
    (r"目录结构|代码目录|文件组织",
     "沿用当前任务计划的输出路径，按功能组织代码，不移动已有文件。"),
    (r"测试框架|单元测试框架|怎么写测试",
     "使用当前仓库已有测试框架；如没有，Python 原型使用 unittest。"),
    (r"前端框架|界面框架",
     "沿用仓库现有框架；空仓库原型用 HTML、CSS 和原生 JavaScript。"),
)
_RISK = re.compile(r"支付|付费|退款|价格|隐私|授权|权限|删除|密码|密钥|保存多久|保留期限|审核|公开|"
                   r"谁可以|谁能|登录|认证|署名|实名|外部|上线|发布|取消|推送|通知|用户.{0,12}(能|可|应|必须)")


def steward_question(id_, item, tasks, requirement):
    original = " ".join(item["question"].split())
    refs = set(item.get("affected_requirement_ids", []))
    explicit = any(
        req["id"] in refs and ("待确认" in req["source"]["section"] or "澄清" in req["source"]["section"]
                              or req["text"].rstrip().endswith(("?", "？")))
        for req in requirement["items"]
    )
    safe = None
    multi_topic = re.search(r"[；;？?].{4,}", original)
    if not explicit and not item.get("options") and not _RISK.search(original) and not multi_topic:
        for pattern, default in _SAFE_DEFAULTS:
            if re.search(pattern, original):
                safe = default
                break
    options = [{"label": opt["text"][:100], "value": opt["text"]}
               for opt in item.get("options", []) if isinstance(opt.get("text"), str) and opt["text"]]
    question = original if len(original) <= 130 else original[:120].rstrip("。！？?；，, ") + "…"
    technical_note = None
    if not explicit and "图文笔记" in original and "编辑" in original and "删除" in original:
        question = "发布后，作者可以编辑或删除自己的笔记吗？"
        options = [{"label": label, "value": value} for label, value in (
            ("可编辑、可删除", "作者可以编辑和删除自己的笔记。"),
            ("只可编辑", "作者可以编辑自己的笔记，首版不支持删除。"),
            ("首版都不支持", "首版不支持编辑或删除已发布的笔记。"),
        )]
        technical_note = "发布字段校验与图片数量、大小上限采用可配置的原型默认值，并在使用说明中列出。"
    elif not explicit and "内容审核" in original and "发布限流" in original:
        question = "首版需要加入内容审核吗？"
        options = [{"label": label, "value": value} for label, value in (
            ("首版先不做", "首版不包含内容审核。"),
            ("首版加入", "首版提供基本的人工审核状态，不接入外部审核服务。"),
        )]
        technical_note = "发布限流采用可配置的原型默认值，不作为独立的首版功能。"
    elif options and original.startswith("多组对该需求提出了不同做法"):
        title = next((req["title"].rstrip("。！？?") for req in requirement["items"] if req["id"] in refs), "此功能")
        question = f"「{title}」首版采用哪种做法？"
    if not question.endswith(("?", "？")):
        question = question.rstrip("。") + "？"
    return dict(id=id_, question=question, detail=original, options=options, default=safe,
                note=technical_note, tasks=tasks)


_HELP = (
    (r"保存|目录|文件.*哪|源码|导出",
     "在顶部点「产物位置」，填写本机文件夹。点「导出项目文件」会写入该目录；想写出真实代码，还要勾选绑定 Git 仓库。"),
    (r"模拟|API|密钥|真实",
     "顶部「模型 API」配置赛马、评审和执行模型。真实写代码另外需要在「产物位置」绑定 Git 仓库并使用绑定后的计划启动。"),
    (r"暂停|停止|继续|恢复",
     "在右侧主理栏暂停派发；正在执行的任务会收尾。空闲后可继续，也可结束本次运行并保留已有产物。"),
    (r"卡|没动|还要|进度|等待|运行|多久",
     "看顶部运行状态。正在执行时会显示当前步骤和耗时；等确认、失败或暂停时没有可靠的完成时间。右侧主理栏提供下一步。"),
    (r"任务|评审|赛马|怎么用|开始|流程|确认",
     "先确认需求并运行赛马，再交接评审。点「主理人整合方案」查看合并稿，确认后进入任务分解；设置产物位置后可启动自动开发。"),
)


def help_answer(question, agent=None, state=None):
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise InvalidReference("请输入一条使用问题（500 字以内）。")
    if state and re.search(r"卡|没动|进度|等待|多久|是不是在跑", question):
        if state["state"] == "not_started":
            return dict(answer="当前项目还没启动开发。先生成任务计划，再点「主理人启动自动开发」。", source="guide")
        status = state["run_id"] + "：已完成 " + str(state["done"]) + " 项。"
        if state["state"] in ("stopped", "completed"):
            reply = "这次运行已经结束。"
        elif state["executing"]:
            reply = "有 " + str(state["executing"]) + " 项正在执行，详情见顶部进度条。"
        elif state["dispatch"] == "paused":
            reply = "当前没有任务在跑，派发已暂停。"
        elif state["waiting"]:
            reply = str(state["waiting"]) + " 项等业务确认，请先处理右栏显示的问题。"
        else:
            reply = "当前没有任务在跑，请查看右栏是否有待派发或审核的任务。"
        return dict(answer=status+reply, source="guide")
    for pattern, answer in _HELP:
        if re.search(pattern, question, re.IGNORECASE):
            return dict(answer=answer, source="guide")
    if agent:
        result = agent("help", {"question": question, "guide": [answer for _, answer in _HELP],
                                "status": state or {}})
        answer = result.get("answer") if isinstance(result, dict) else None
        if isinstance(answer, str) and answer.strip():
            return dict(answer=answer.strip()[:600], source="model")
    return dict(answer="暂时没有找到对应帮助。可以问我怎么配置 API、启动开发、了解卡点或查看产物位置。",
                source="guide")
