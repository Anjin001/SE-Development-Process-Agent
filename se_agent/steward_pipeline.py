"""Durable, chat-led orchestration for the integrated workbench."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from itertools import combinations
import json
import logging
from pathlib import Path
import re
from threading import Lock, Thread
from time import sleep, time
from uuid import uuid4

from .agent_client import AgentError
from .limits import MAX_INPUT_BYTES, MODEL_TIMEOUT_SECONDS
from .contracts import DomainError, InvalidReference, InvalidState, NotFound
from .intake import build_snapshot, default_group_prompts, run_group
from .synthesis_preview import PreviewError
from .team_plan import fallback_modules, repair_tasks, team_tasks, validate_modules

STAGES = {
    "groups": "设计讨论路线", "race": "多组独立出案", "evaluate": "独立评审",
    "merge": "整合方案", "decisions": "主理人补齐决策", "confirm": "发布需求规格",
    "plan": "拆任务与排期", "start": "启动开发", "develop": "开发与复核", "delivery": "整理交付",
}
GATES = {"spec": "方案确认", "plan": "开工确认", "verification": "集成验收确认", "delivery": "交付确认"}
RUNNING = {"running", "pausing"}
DEFAULT_RACE_PARALLEL = 3
BASE = Path(__file__).resolve().parents[1]


def options(body, previous=None):
    result = dict(gates=[], group_count=0, prompts=[], parallel=0, race_parallel=DEFAULT_RACE_PARALLEL,
                  guidance="", directory="", mode="auto")
    if previous:
        result.update(previous)
    if not isinstance(body, dict):
        raise InvalidReference("流程设置格式无效。")
    for field in result:
        if field in body:
            result[field] = body[field]
    gates = result["gates"]
    if not isinstance(gates, list) or any(not isinstance(x, str) or x not in GATES for x in gates):
        raise InvalidReference("确认点无效。")
    result["gates"] = list(dict.fromkeys(gates))
    count = result["group_count"]
    if type(count) is not int or (count != 0 and not 2 <= count <= 5):
        raise InvalidReference("分组数应为自动，或 2 到 5 组。")
    parallel = result["parallel"]
    if type(parallel) is not int or not 0 <= parallel <= 6:
        raise InvalidReference("开发并发数应为自动，或 1 到 6。")
    race_parallel = result["race_parallel"]
    if type(race_parallel) is not int or not 1 <= race_parallel <= 5:
        raise InvalidReference("出案并发数应为 1 到 5。")
    prompts = result["prompts"]
    if not isinstance(prompts, list) or len(prompts) > 5 or any(not isinstance(p, str) or len(p) > 3000 for p in prompts):
        raise InvalidReference("手动提示词最多五组，每组不超过 3000 字。")
    if any(not isinstance(result[k], str) or len(result[k]) > limit
           for k, limit in {"guidance": 3000, "directory": 500, "mode": 20}.items()):
        raise InvalidReference("流程偏好或产物目录格式无效。")
    if result["mode"] not in ("auto", "mock", "real"):
        raise InvalidReference("代码执行模式无效。")
    return result


def markdown_for(text):
    text = text.strip()
    if not text:
        raise InvalidReference("请先告诉主理人要做什么。")
    # A lone Markdown heading is also a valid request in the conversation.
    if not any(line.strip() and not re.match(r"^\s*#+(?:\s|$)", line) for line in text.splitlines()):
        title = re.sub(r"^\s*#+\s*", "", text.splitlines()[0]).strip()
        return f"# 项目需求\n\n- {title}" if title else text
    return text


def group_profiles(requirement, settings, agent):
    forced = settings["group_count"]
    if agent:
        value = agent("steward_groups", dict(requirement=requirement, count=forced or None,
                                             guidance=settings["guidance"]))
        profiles = value.get("groups") if isinstance(value, dict) else None
        if (not isinstance(profiles, list) or not 2 <= len(profiles) <= 5
                or forced and len(profiles) != forced
                or any(not isinstance(r, dict) or not isinstance(r.get("focus"), str)
                       or not r["focus"].strip() or not isinstance(r.get("prompt"), str)
                       or not r["prompt"].strip() for r in profiles)):
            raise AgentError("分组设计没有返回有效路线，主理人会保留需求，可在对话中重试。")
    else:
        pool = [("最小交付", default_group_prompts()["A"]),
                ("用户体验与异常", default_group_prompts()["B"]),
                ("工程成本与扩展", "优先降低依赖和交付成本，说明性能、数据和可维护性取舍。"),
                ("可测试性", "围绕边界条件和可复现的验收步骤给出独立方案。"),
                ("运营与可访问性", "围绕运行维护、错误提示和可访问性给出独立方案。")]
        profiles = [dict(focus=focus, prompt=prompt) for focus, prompt in pool[:forced or (3 if len(requirement["items"]) > 4 else 2)]]
    for idx, group in enumerate(profiles):
        group["id"] = chr(ord("A") + idx)
        group["focus"] = group["focus"].strip()[:120]
        override = settings["prompts"][idx].strip() if idx < len(settings["prompts"]) else ""
        group["prompt"] = (override or group["prompt"].strip())[:3000]
    if len({g["prompt"] for g in profiles}) < len(profiles):
        raise InvalidReference("讨论路线的提示词需要有差异。可清除自定义提示词后重试。")
    return profiles


def decisions_for(requirement, spec, agent, guidance=""):
    pending = [item for item in spec["open_items"] if item["resolution"] is None]
    if not pending:
        return {}
    if agent:
        context = dict(requirements=requirement["items"], current_decisions=spec["decisions"],
                       issues=pending, guidance=guidance)
        for _ in range(2):
            result = agent("steward_decisions", context)
            rows = result.get("decisions") if isinstance(result, dict) else None
            ids = {item["id"] for item in pending}
            if (isinstance(rows, list) and len(rows) == len(ids)
                    and all(isinstance(r, dict) and isinstance(r.get("id"), str)
                            and isinstance(r.get("choice"), str) and r["choice"].strip()
                            and isinstance(r.get("reason"), str) and r["reason"].strip() for r in rows)
                    and {r["id"] for r in rows} == ids):
                return {r["id"]: dict(choice=r["choice"].strip()[:1000], reason=r["reason"].strip()[:500]) for r in rows}
            context["validation"] = "请严格返回每一个问题的原编号，而且每个问题只能有一条具体决定。"
        raise AgentError("主理人未能为每个待决问题生成有效决定，未发布不完整规格。")
    from .workspace_experience import steward_question
    result = {}
    for item in pending:
        suggestion = steward_question(item["id"], item, [], requirement)
        original = item["question"]
        candidate = next((choice["value"] for choice in suggestion["options"]
                          if not re.search(r"自动取消|自动删除|公开隐私", choice["value"])), "")
        default = (suggestion["default"] or
                   ("保留已存在的预约，不允许对停用设备新增预约。"
                    if "自动取消" in original and "预约" in original else "") or candidate or
                   "按原需求的明确行为实现首版；尚未明确的扩展功能默认关闭，参数保留为本地可配置。")
        if suggestion["note"]:
            default += " " + suggestion["note"]
        result[item["id"]] = dict(choice=default, reason="离线模拟按最小范围和可撤销默认值处理。")
    return result


def resolve_spec(review, agent, guidance=""):
    spec = review["specification"]
    resolutions = decisions_for(review["requirement"], spec, agent, guidance)
    decisions, items = deepcopy(spec["decisions"]), deepcopy(spec["open_items"])
    occupied = {decision["id"] for decision in decisions}
    groups = [draft["id"] for draft in review["drafts"]]
    for item in items:
        if item["id"] not in resolutions:
            continue
        resolution = resolutions[item["id"]]
        n = next(i for i in range(1, len(occupied)+2) if f"DEC-{i:02d}" not in occupied)
        id_ = f"DEC-{n:02d}"
        occupied.add(id_)
        refs = item["affected_requirement_ids"]
        sources = list(dict.fromkeys(o["draft_id"] for o in item.get("options", [])))
        if not sources:
            sources = [draft["id"] for draft in review["drafts"]
                       if any(set(refs).intersection(c["requirement_ids"]) for c in draft["claims"])]
        decisions.append(dict(id=id_, scope="主理决定 · " + item["question"][:100], **resolution,
                              requirement_ids=refs, draft_ids=sources or groups))
        item.update(resolution=resolution["choice"], decision_id=id_)
    return dict(markdown=spec["markdown"], decisions=decisions, open_items=items,
                expected_version=spec["version"],
                notes_markdown=(spec.get("notes_markdown", "") + "\n\n主理人代为处理本轮未决选择；每项决定保留来源和理由。").strip())


class StewardPipeline:
    def __init__(self, workspace, agent, review_lock, output_root=None):
        self.workspace = workspace
        self.db = workspace.database
        self.agent = agent
        self.review_lock = review_lock
        self.output_root = Path(output_root or BASE.parent / "SE-Agent-Projects").resolve()
        self.worker_lock = Lock()
        self.threads = set()
        self.db.db.execute("CREATE TABLE IF NOT EXISTS steward_jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        with self.db.tx():
            for record in self._all():
                if record["status"] in RUNNING:
                    record.update(status="paused", activity="服务重启，等待从上次检查点继续。")
                    if record["run_id"]:
                        run = self.db.snapshot(record["run_id"])
                        if run["state"] == "active" and run["dispatch"] == "active":
                            self.db.set_dispatch(run["id"], True)
                    for route in record.get("race_progress", {}).values():
                        if route["status"] == "running":
                            route.update(status="queued", step="服务重启，等待继续出案。")
                    self._save(record)

    def _all(self):
        return [json.loads(row["payload"]) for row in self.db.db.execute("SELECT payload FROM steward_jobs ORDER BY rowid DESC")]

    def _read(self, key):
        row = self.db.db.execute("SELECT payload FROM steward_jobs WHERE id=?", (key,)).fetchone()
        if row is None:
            raise NotFound("找不到这次主理流程。")
        return json.loads(row["payload"])

    def _save(self, record):
        self.db.db.execute("INSERT INTO steward_jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           (record["id"], json.dumps(record, ensure_ascii=False)))

    def _update(self, key, **fields):
        with self.db.tx():
            data = self._read(key)
            data.update(fields)
            self._save(data)
            return data

    def _event(self, key, title, info=None, timeout=0):
        with self.db.tx():
            data = self._read(key)
            now = time()
            changes = dict(time=now, title=title, stage=data["stage"])
            if info:
                changes["detail"] = info
            trail = (data["events"] + [changes])[-80:]
            data.update(events=trail, activity=title, activity_at=now, timeout=timeout)
            self._save(data)

    def _message(self, key, role, text):
        with self.db.tx():
            data = self._read(key)
            data["messages"] = (data["messages"] + [dict(role=role, text=text, time=time())])[-70:]
            self._save(data)

    def public(self, key):
        with self.db._lock:
            record = self._read(key)
            output = {k: record[k] for k in ("id", "markdown", "phase", "stage", "status", "error", "activity",
                      "activity_at", "timeout", "events", "messages", "settings", "gate",
                      "project_id", "run_id", "summary", "target", "delivery")}
            output["settings"].setdefault("race_parallel", DEFAULT_RACE_PARALLEL)
            progress = record.get("race_progress", {})
            output["groups"] = []
            for group in record["groups"]:
                id_ = group["id"]
                finished = id_ in record["drafts"]
                baseline = {"status":"done" if finished else "queued", "step":"已提交" if finished else "待派发"}
                live = progress.get(id_, {}) if not finished else {}
                output["groups"].append(dict(id=id_, focus=group["focus"], prompt=group["prompt"],
                                             finished=finished, **(baseline | live)))
            if record["run_id"]:
                if record["status"]=="failed":
                    evidence = self._evidence(record)
                    if evidence and evidence[0]["reason"]:
                        item = evidence[0]
                        output["error"] = f"{item['task']}：{item['reason']}"
                run = self.workspace.run(record["project_id"], record["run_id"])
                tasks = list(run["tasks"].values())
                plan = self.db.get_plan(record["project_id"], run["plan_ref"]["id"], run["plan_ref"]["version"])
                pending = [s for s in plan["schedule"] if run["tasks"][s["task_id"]]["state"] != "done"]
                planned = (max(p["early_finish_seconds"] for p in pending)-min(p["early_start_seconds"] for p in pending)) if pending else None
                output["execution"] = dict(done=sum(t["state"] == "done" for t in tasks), total=len(tasks),
                    running=[dict(id=t["task_id"], step=(t.get("activity") or [{"label":"执行任务"}])[-1]["label"])
                             for t in tasks if t["state"] == "running"],
                    estimated_seconds=planned if run["dispatch"] == "active" and any(t["state"] == "running" for t in tasks) else None)
            return output

    def list(self):
        with self.db._lock:
            return [dict(id=row["id"], title=row["title"], status=row["status"], created=row["created"])
                    for row in self._all()][:25]

    def start(self, markdown, settings, project_id=None, *, phase=1, prior_messages=None):
        config = options(settings or {})
        if project_id:
            with self.review_lock, self.db._lock:
                project = self.workspace.project(project_id)
                if project["run"] and project["run"]["state"] == "active":
                    raise InvalidState("请先暂停并结束旧的手动运行，或把需求作为新一轮交给主理人。")
                review = project["review"]
                markdown = review["requirement"]["markdown"]
                spec = review["specification"]
                if spec and spec["status"] == "confirmed" and any(i["resolution"] is None for i in spec["open_items"]):
                    raise InvalidState("这份旧规格已确认且有待决项，请作为新一轮交给主理人。")
                stage = "plan" if spec and spec["status"] == "confirmed" else "decisions" if spec else "merge" if review["evaluations"] else "evaluate"
        else:
            if not isinstance(markdown, str) or not markdown.strip() or len(markdown.encode("utf-8")) > MAX_INPUT_BYTES:
                raise InvalidReference("请提供需求正文（最多 1 MB（UTF-8））。")
            markdown = markdown_for(markdown)
            stage = "groups"
        id_ = uuid4().hex
        requirement = build_snapshot(markdown, "REQ-" + id_[:8], 1)
        now = time()
        with self.db.tx():
            if project_id and any(job["project_id"] == project_id and job["status"] not in ("completed","cancelled")
                                  for job in self._all()):
                raise InvalidState("这份方案已有主理流程，可在主理人对话中继续。")
            self._save(dict(id=id_, title=requirement["items"][0]["title"], markdown=markdown, phase=phase, created=now,
                            stage=stage, status="running", error=None, activity="收到需求", activity_at=now, timeout=0,
                            events=[], messages=prior_messages or [dict(role="user", text=markdown, time=now)],
                            settings=config, gate=None, accepted=[], project_id=project_id, run_id=None,
                            summary=None, target=None, delivery=None, groups=[], drafts={}, resolutions={}, retries={}))
        self._message(id_, "steward", "收到。我会按所选流程推进，并在这里更新进度。")
        self._spawn(id_)
        return self.public(id_)

    def _spawn(self, id_):
        with self.worker_lock:
            if id_ in self.threads:
                return
            self.threads.add(id_)
            Thread(target=self._background, args=(id_,), daemon=True).start()

    def _background(self, key):
        try:
            self._advance(key)
        except (DomainError, PreviewError, AgentError, ValueError, OSError) as err:
            with self.db._lock:
                record = self._read(key)
            if record["status"] == "cancelled":
                return
            self._pause_run(record)
            message = getattr(err, "message", str(err))
            self._update(key, status="failed", error=message[:700], timeout=0)
            self._event(key, "主理流程未能推进", message[:700])
        except Exception:
            logging.exception("Unexpected steward pipeline error (%s)", key)
            with self.db._lock:
                record = self._read(key)
            if record["status"] != "cancelled":
                self._pause_run(record)
                self._update(key, status="failed", error="主理流程遇到内部错误，使用「重试」从当前检查点继续。", timeout=0)
        finally:
            with self.worker_lock:
                self.threads.discard(key)
            with self.db._lock:
                state = self._read(key)["status"]
            if state in RUNNING:
                self._spawn(key)

    def _gate(self, row, name, *, required=False):
        with self.db.tx():
            current = self._read(row["id"])
            if current["status"] != "running":
                return True
            if (required or name in current["settings"]["gates"]) and name not in current["accepted"]:
                current.update(status="gate", gate=name, timeout=0)
                self._save(current)
                self._message(row["id"], "steward", f"已到你选的「{GATES[name]}」点。可以直接告诉我继续，或说要改哪里。")
                return True
        return False

    def _advance(self, key):
        while True:
            with self.db._lock:
                row = self._read(key)
            if row["status"] == "pausing":
                self._pause_run(row)
                with self.db.tx():
                    current = self._read(key)
                    if current["status"] == "pausing":
                        current.update(status="paused", timeout=0)
                        self._save(current)
                return
            if row["status"] != "running":
                return
            stage = row["stage"]
            pid = row["project_id"]
            cfg = row["settings"]
            agent = self.agent()
            budget = MODEL_TIMEOUT_SECONDS if agent else 0
            event = lambda title, sec=0: self._event(key, title, timeout=sec)
            forward = lambda following, **fields: self._update(key, stage=following, **fields)
            req = None
            if stage in ("groups", "race"):
                req = build_snapshot(row["markdown"], "REQ-" + key[:8], row["phase"])
            if stage == "groups":
                event("主理人设计有差异的讨论路线", budget)
                groups = group_profiles(req, cfg, agent)
                event(f"已确定 {len(groups)} 条讨论路线")
                forward("race", groups=groups)
            elif stage == "race":
                drafts = self._race(row, req, agent)
                if drafts is None:
                    continue
                with self.review_lock, self.db.tx():
                    if self._read(key)["status"] != "running":
                        continue
                    project = self.workspace.reviews.create(dict(requirement=req, drafts=drafts))
                    forward("evaluate", project_id=project["id"])
            elif stage == "evaluate":
                event("开始独立评审各组的可行性、价值和成本", budget)
                with self.review_lock:
                    self.workspace.reviews.change(pid, "evaluate", {}, progress=event)
                    forward("merge")
            elif stage == "merge":
                event("主理人比较方案并整合取舍", budget)
                with self.review_lock:
                    review = self.workspace.reviews.snapshot(self.workspace.reviews.get(pid))
                    self.workspace.reviews.change(pid, "propose", {"selection":"merge",
                        "expected_version": review["latest_version"]}, progress=event)
                    forward("decisions")
            elif stage == "decisions":
                event("主理人处理开放项，形成可执行的原型规则", budget)
                with self.review_lock:
                    review = self.workspace.reviews.snapshot(self.workspace.reviews.get(pid))
                payload = resolve_spec(review, agent, cfg["guidance"])
                with self.review_lock:
                    if any(o["resolution"] is None for o in review["specification"]["open_items"]):
                        review = self.workspace.reviews.change(pid, "save", payload, actor="steward")
                spec = review["specification"]
                forward("confirm", summary=self._summary(spec))
            elif stage == "confirm":
                if self._gate(row, "spec"):
                    return
                event("规格已整理，主理人发布确认版")
                with self.review_lock:
                    review = self.workspace.reviews.snapshot(self.workspace.reviews.get(pid))
                    if review["specification"]["status"] != "confirmed":
                        self.workspace.reviews.change(pid, "confirm", {
                            "expected_version":review["latest_version"]}, actor="steward")
                    forward("plan")
            elif stage == "plan":
                event("生成任务、连接依赖并计算排期")
                self._prepare_plan(key, pid, cfg, agent)
                if self._current_status(key) == "running":
                    forward("start")
            elif stage == "start":
                if self._gate(row, "plan"):
                    return
                event("按依赖和并发上限自动派发任务")
                with self.review_lock, self.db.tx():
                    plan = self.workspace.plans.latest(pid)["plan"]
                    if plan["base_commit"] and not agent:
                        raise InvalidState("任务清单已保存。请先配置模型 API，再继续开工，避免启动空的真实执行。")
                    run = self.db.create_run(pid, plan["id"], plan["version"],
                                             "manual" if row.get("manual_debug") else "auto")
                    forward("develop", run_id=run["id"])
            elif stage == "develop":
                if self._development(row, agent):
                    forward("delivery")
                else:
                    sleep(.6)
            elif stage == "delivery":
                if self._gate(row, "delivery"):
                    return
                event("整理最终交付清单")
                with self.review_lock, self.db._lock:
                    data = self.workspace.project(pid)
                    target = data["target"]
                    artifact = self.workspace.delivery.export(data) if target["path"] else None
                    if artifact and target["repo"] and data["run"]:
                        run = data["run"]
                        planned = self.db.get_plan(pid,run["plan_ref"]["id"],run["plan_ref"]["version"])["plan"]
                        integrators = [t["id"] for t in planned["tasks"]
                                       if t["phase"] in ("integration", "verification", "documentation")]
                        for id_ in reversed(integrators):
                            commit = next((a for a in run["tasks"][id_]["artifacts"] if a["kind"]=="git_commit"
                                           and a["status"]=="approved"),None)
                            if commit:
                                artifact.update(git_commit=commit["commit_sha"],
                                    git_branch=f"attempt/{run['id']}/{commit['attempt_id']}")
                                break
                self._update(key, delivery=artifact, target=target, status="completed", timeout=0)
                mode = "真实代码保存在目标 Git 仓库的任务分支。" if target["repo"] else "本轮为模拟执行，没有生成真实源代码。"
                self._message(key, "steward", "本轮已经交付。"+mode+" 可以告诉我要改哪里，我会创建下一轮。")
                return

    def _race_progress(self, key, group_id, status, step, timeout=0, draft=None):
        with self.db.tx():
            row = self._read(key)
            trail = row.setdefault("race_progress", {})
            trail[group_id] = dict(status=status, step=step, since=time(), timeout=timeout)
            if draft is not None:
                row["drafts"][group_id] = draft
            self._save(row)
            self._event(key, step, timeout=timeout)

    def _race(self, row, requirement, agent):
        key = row["id"]
        pending = iter(group for group in row["groups"] if group["id"] not in row["drafts"])
        limit = row["settings"].get("race_parallel", DEFAULT_RACE_PARALLEL)
        active, failures = {}, {}
        exhausted = False

        def from_group(group):
            kwargs = {"agent": agent} if agent else {}
            id_ = group["id"]
            return run_group(deepcopy(requirement), id_, prompt=group["prompt"], **kwargs,
                progress=lambda label, seconds=0: self._race_progress(key, id_, "running", label, seconds))["draft"]

        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="steward-race") as pool:
            while True:
                while len(active) < limit and not exhausted:
                    with self.db.tx():
                        if self._read(key)["status"] != "running":
                            break
                        group = next(pending, None)
                        if not group:
                            exhausted = True
                            break
                        id_ = group["id"]
                        self._race_progress(key, id_, "running", id_+" 组开始出案")
                        active[pool.submit(from_group, group)] = id_
                if not active:
                    break
                done, _ = wait(active, timeout=.2, return_when=FIRST_COMPLETED)
                for future in done:
                    id_ = active.pop(future)
                    try:
                        draft = future.result()
                    except (AgentError, DomainError, TypeError, ValueError) as err:
                        message = getattr(err, "message", str(err))[:400]
                        failures[id_] = message
                        self._race_progress(key, id_, "failed", id_+" 组："+message)
                    except Exception:
                        logging.exception("Unable to generate draft for group %s", id_)
                        failures[id_] = "执行遇到内部错误"
                        self._race_progress(key, id_, "failed", id_+" 组执行遇到内部错误")
                    else:
                        self._race_progress(key, id_, "done", id_+" 组已交案", draft=draft)
        if self._current_status(key) != "running":
            return None
        if failures:
            detail = "；".join(id_+" 组："+failures[id_] for id_ in sorted(failures))
            raise AgentError(detail+"。重试只会处理未交案的小组。")
        with self.db._lock:
            record = self._read(key)
            return [record["drafts"][group["id"]] for group in record["groups"]]

    def _summary(self, spec):
        return dict(version=spec["version"], markdown=spec["markdown"],
                    decisions=[dict(scope=d["scope"], choice=d["choice"], reason=d["reason"]) for d in spec["decisions"]])

    def _current_status(self, key):
        with self.db._lock:
            return self._read(key)["status"]

    def _prepare_plan(self, key, pid, cfg, agent):
        with self.review_lock, self.db.tx():
            data = self.workspace.project(pid)
            review = data["review"]
            target = data["target"]
            real = cfg["mode"] != "mock" and bool(agent or target["repo"])
            if cfg["mode"] == "real" and not real:
                raise InvalidState("你选择了真实开发，请配置模型 API，然后对主理人说「重试」。")
            if (not target["path"] and (cfg["directory"] or real)) or (
                    target["path"] and real and not target["repo"]):
                path = cfg["directory"].strip() or target["path"] or str(self.output_root / pid[:12])
                data = self.workspace.configure_target(pid, path, real)
                target = data["target"]
            if not real and target["repo"]:
                raise InvalidState("这份方案绑定了真实 Git 仓库；要模拟时请从对话新建一轮。")
            plan = self.workspace.plans.generate(review, review["specification"]["version"])["plan"]
        requirements = review["requirement"]["items"]
        modules = fallback_modules(requirements)
        spec_version = review["specification"]["version"]
        with self.db._lock:
            checkpoint = self._read(key).get("plan_checkpoint")
        if checkpoint and checkpoint["spec_version"] != spec_version:
            checkpoint = None
        if agent and not checkpoint:
            self._event(key,"按功能划分工作包、对齐接口和文件所有权",timeout=MODEL_TIMEOUT_SECONDS)
            context = dict(requirements=requirements, decisions=review["specification"]["decisions"],
                guidance=cfg["guidance"], suggested=[dict(slug=m["slug"],scope=m["scope"]) for m in modules],
                minimum_modules=min(len(modules),4))
            try:
                proposed = agent("steward_plan", context)
                modules = validate_modules(proposed.get("modules"),requirements,min(len(modules),4))
            except (AgentError, TypeError, ValueError, AttributeError) as exc:
                self._event(key, "模型拆分未通过结构检查，生成备用工作包", str(exc)[:600])
        tasks = checkpoint["tasks"] if checkpoint else team_tasks(review,modules)
        parallel = cfg["parallel"] or min(3, sum(t["phase"] == "development" for t in tasks))
        if agent and target["repo"]:
            review_input = dict(task={"title": "开工前复核任务编排",
                                      "completion_condition": "每条需求有实现与验收路径，每项前端能力有后端责任，交接完成后才能并发开发。"},
                                requirements=requirements, decisions=review["specification"]["decisions"])
            feedback = checkpoint.get("feedback", "") if checkpoint else ""
            for round_ in range(4):
                if self._current_status(key) != "running":
                    return
                self._update(key, plan_checkpoint=dict(spec_version=spec_version, tasks=tasks, feedback=feedback))
                if feedback:
                    self._event(key, f"按退回意见修复已有任务 · 第 {round_+1} 次", feedback, MODEL_TIMEOUT_SECONDS)
                    correction = agent("steward_plan_repair", dict(review_input, tasks=tasks, review_feedback=feedback))
                    try:
                        tasks = repair_tasks(tasks, correction)
                    except ValueError as exc:
                        feedback = (feedback + f"\n上次修复结果无效：{exc}")[:1500]
                        continue
                    self._update(key, plan_checkpoint=dict(spec_version=spec_version, tasks=tasks, feedback=""))
                self._event(key, "复核任务覆盖、接口责任与验收依赖", timeout=MODEL_TIMEOUT_SECONDS)
                verdict = agent("steward_review", dict(review_input, tasks=tasks))
                if not isinstance(verdict, dict) or type(verdict.get("approved")) is not bool:
                    feedback = ""
                    reason = "模型连续未提供有效的复核结论"
                    continue
                reason = str(verdict.get("reason") or "没有说明理由")[:1200]
                if verdict["approved"]:
                    self._event(key, "计划复核通过", reason)
                    break
                feedback = reason
                self._event(key, "计划被退回，自动修复相关任务", feedback)
            else:
                self._update(key, plan_checkpoint=dict(spec_version=spec_version, tasks=tasks, feedback=feedback))
                raise InvalidState(f"计划复核达到上限，已保留最新草稿：{feedback or reason}")
        if self._current_status(key) != "running":
            return
        with self.review_lock:
            saved = self.workspace.plans.save(review,plan["version"],tasks,parallel)
            plan = saved["plan"]
        self._event(key,"计划已发布：技术交接 → 功能开发 → 集成 → 验收 → 交付")
        self._update(key, target=target, plan_checkpoint=None, summary={**self._summary(review["specification"]),
            "tasks": [dict(id=t["id"], title=t["title"], depends_on=t["depends_on"],
                           estimate_seconds=t["estimate_seconds"],outputs=[a["path"] for a in t["outputs"]])
                      for t in plan["tasks"]],
            "mode":"real" if plan["base_commit"] else "mock", "plan_version":plan["version"]})

    def _development(self, row, agent):
        key, pid, rid = row["id"], row["project_id"], row["run_id"]
        with self.db._lock:
            run = self.workspace.run(pid, rid)
            plan = self.db.get_plan(pid, run["plan_ref"]["id"], run["plan_ref"]["version"])["plan"]
        verification_ids = {task["id"] for task in plan["tasks"] if task["phase"] == "verification"}
        if run["state"] == "completed":
            return True
        if run["state"] != "active":
            raise InvalidState("开发运行已结束，可在对话中发起新一轮。")
        if (row.get("summary") or {}).get("mode") == "real" and not agent:
            raise InvalidState("真实开发已暂停。配置模型 API 后，可以从当前开发任务继续。")
        if run["dispatch"] == "paused" and run["mode"] != "manual":
            with self.db.tx():
                self.db.set_dispatch(rid, False)
        exhausted = []
        can_advance = any(task["state"] in ("ready", "running", "review") for task in run["tasks"].values())
        for task in run["tasks"].values():
            if self._current_status(key) != "running":
                return False
            id_ = task["task_id"]
            if task["state"] == "failed":
                if run["mode"] == "manual":
                    continue
                detail = self._failure_reason(rid,id_,task) or "没有记录详细失败原因"
                retries = row["retries"]
                if retries.get(id_, 0) >= 1:
                    exhausted.append(f"{id_}：{detail}")
                    continue
                with self.db.tx():
                    self.db.retry(rid, id_)
                    retries[id_] = retries.get(id_, 0) + 1
                    if id_ in verification_ids:
                        self._update(key, retries=retries,
                                     accepted=[gate for gate in row["accepted"] if gate != "verification"])
                    else:
                        self._update(key, retries=retries)
                can_advance = True
                self._event(key, f"{id_} 失败，自动重试一次", detail)
            elif task["state"] == "review":
                acceptance = id_ in verification_ids
                real_acceptance = acceptance and bool(plan["base_commit"])
                if acceptance and self._gate(row, "verification", required=real_acceptance):
                    return False
                if run["mode"] != "manual" and not real_acceptance:
                    self._review_task(row, task, agent)
        if can_advance:
            return False
        if exhausted:
            raise InvalidState("；".join(exhausted)[:630]+"。主理人已重试一次。")
        if any(task["missing_input_ids"] for task in run["tasks"].values()):
            raise InvalidState("当前计划仍绑定未解决的问题，请让主理人重新生成方案。")
        return False

    def _failure_reason(self, run_id, task_id, task):
        attempt = (task["attempts"] or [{}])[-1]
        raw = attempt.get("error")
        if raw:
            try:
                raw = json.loads(raw) if isinstance(raw,str) else raw
                if isinstance(raw,dict) and raw.get("message"):
                    return str(raw["message"])[:500]
                if isinstance(raw,dict) and raw.get("problems"):
                    return "；".join(str(p.get("message","校验失败")) for p in raw["problems"] if isinstance(p,dict))[:500]
            except ValueError:
                pass
        with self.db._lock:
            review = self.db.db.execute(
                "SELECT note FROM review_records WHERE run_id=? AND task_id=? AND decision='reject' ORDER BY rowid DESC LIMIT 1",
                (run_id,task_id)).fetchone()
        return str(review["note"])[:500] if review else ""

    def _evidence(self, row):
        if not row["run_id"]:
            return []
        with self.db._lock:
            run = self.workspace.run(row["project_id"],row["run_id"])
            plan = self.db.get_plan(row["project_id"],run["plan_ref"]["id"],run["plan_ref"]["version"])["plan"]
            definitions = {t["id"]:t for t in plan["tasks"]}
        result = []
        for id_,task in run["tasks"].items():
            if task["state"] != "failed":
                continue
            result.append(dict(task=id_, title=definitions[id_]["title"], attempts=len(task["attempts"]),
                               reason=self._failure_reason(run["id"],id_,task)))
        return result

    def _review_task(self, row, task, agent):
        key, pid, rid = row["id"], row["project_id"], row["run_id"]
        id_ = task["task_id"]
        drafts = [a for a in task["artifacts"] if a["status"] == "draft"]
        if not drafts:
            return
        self._event(key, f"主理人复核 {id_} 的产物", timeout=MODEL_TIMEOUT_SECONDS if agent else 0)
        note = "离线模拟：产物已通过编排器的结构检查。"
        verdict = "approve"
        if agent:
            with self.db._lock:
                plan_ref = self.db.snapshot(rid)["plan_ref"]
                definition = next(t for t in self.db.plan_tasks(pid, plan_ref["id"], plan_ref["version"]) if t["id"] == id_)
                verifier = self.db.verifier_for(pid)
            summaries = []
            inspected = set()
            for item in drafts:
                commit = item.get("commit_sha")
                if commit and commit in inspected:
                    continue
                if commit:
                    inspected.add(commit)
                content = (item.get("content") or "")[:12000]
                if verifier and item.get("commit_sha") and item["changed_paths"]:
                    paths = item["changed_paths"]
                    budget = max(400, 15500 // len(paths))
                    files = []
                    for path in paths:
                        source = verifier._git("show", "--format=", "--unified=2", commit, "--", path)
                        excerpt = source
                        if len(source) > budget:
                            head = budget * 2 // 3
                            excerpt = (source[:head] + "\n[中间正文省略]\n" + source[-(budget-head):])
                        files.append(dict(path=path, characters=len(source), excerpt=excerpt,
                                          excerpt_is_partial=len(source)>budget))
                    summaries.append(dict(kind=item["kind"], paths=paths, files=files))
                    continue
                summaries.append(dict(kind=item["kind"], paths=item["changed_paths"], content=content))
            result = agent("steward_review", dict(task=definition, artifacts=summaries))
            if (not isinstance(result, dict) or type(result.get("approved")) is not bool
                    or not isinstance(result.get("reason"), str) or not result["reason"].strip()):
                raise AgentError(f"{id_} 的自动复核未返回有效结论。")
            note = result["reason"].strip()[:650]
            verdict = "approve" if result["approved"] else "reject"
        with self.db.tx():
            self.db.review(rid, id_, verdict, {a["output_id"]: a["version"] for a in drafts},
                           note=note, actor_id="steward")
        self._event(key, f"{id_} {'已通过主理复核' if verdict == 'approve' else '被主理退回'}", note)

    def _pause_run(self, row):
        if row["run_id"]:
            with self.db.tx():
                run = self.db.snapshot(row["run_id"])
                if run["state"] == "active" and run["dispatch"] == "active":
                    self.db.set_dispatch(run["id"], True)


    def chat(self, key, message, command=None, settings=None):
        if not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > MAX_INPUT_BYTES:
            raise InvalidReference("请输入给主理人的消息（最多 1 MB（UTF-8））。")
        message = message.strip()
        with self.db._lock:
            row = self._read(key)
        intent = command
        if not intent:
            count = re.search(r"(?:分组数改成|分成|改成|设为)([2-5二三四五])\s*组", message)
            parallel = re.search(r"并发(?:数|上限)?(?:改成|设为|改为|用)?\s*([1-6一二三四五六])", message)
            if count or parallel:
                convert = lambda x: "一二三四五六".index(x)+1 if x in "一二三四五六" else int(x)
                settings = {}
                if count:
                    settings["group_count"] = convert(count[1])
                if parallel:
                    settings["parallel"] = convert(parallel[1])
                intent = "redo"
            gate_names = {"方案确认":"spec", "开工确认":"plan", "集成验收确认":"verification", "交付确认":"delivery"}
            matched = [value for label, value in gate_names.items() if label in message]
            if not intent and (matched or "全部确认" in message) and re.search(r"打开|开启|关闭|取消",message):
                current = set(row["settings"]["gates"])
                matched = set(matched or GATES)
                enabled = not bool(re.search(r"关闭|取消",message))
                self._message(key, "user", message)
                self._message(key, "steward", "已更新这一轮的确认点。")
                return self.set_gates(key, sorted(current | matched if enabled else current - matched))
            words = message.strip("。！!？? ")
            if intent:
                pass
            elif words in {"继续","开始","同意","确认","通过","确认继续","继续推进"}:
                intent = "continue"
            elif words in {"暂停","先暂停","停一下","别继续","停止派发"}:
                intent = "pause"
            elif words in {"重试","再试一次","重新尝试"}:
                intent = "retry"
            elif words in {"重跑","重新跑","重新开始"}:
                intent = "redo"
            elif (words in {"重拆任务","重新拆分任务","重新编排","按模块重新拆分"}
                  or re.search(r"(?:重做|重新|重跑|从).{0,18}(?:编排|任务分解)", words)):
                intent = "replan_manual" if "单步" in words else "replan"
            elif re.match(r"^(改成|把|请改|想改|修改|增加|删除|去掉|加上|调整|改一下|别做)", message):
                intent = "revise"
            elif re.search(r"到哪一?步|还要|进度|还在跑|没动|为什么|状态|卡在哪", message):
                intent = "status"
            elif self.agent():
                parsed = self.agent()("steward_chat", dict(message=message, stage=STAGES[row["stage"]],
                    status=row["status"], recent=[e["title"] for e in row["events"][-5:]],
                    failure_evidence=self._evidence(row),
                    conversation=[m["text"][:320] for m in row["messages"][-6:]]))
                kind = parsed.get("intent") if isinstance(parsed, dict) else None
                intent = kind if kind in ("revise", "pause", "continue", "retry", "redo", "status") else "ask"
                if intent == "ask":
                    answer = parsed.get("reply") if isinstance(parsed, dict) else None
                    self._message(key, "user", message)
                    self._message(key, "steward", answer[:700] if isinstance(answer, str) and answer.strip()
                                  else "可以直接告诉我要改什么，也可以说暂停、继续或查看进度。")
                    return self.public(key)
            else:
                intent = "ask"
        if intent not in ("pause", "continue", "retry", "status", "ask", "revise", "redo", "replan", "replan_manual"):
            raise InvalidReference("未知主理人指令。")
        self._message(key, "user", message)
        if intent in ("replan", "replan_manual"):
            if not row["project_id"] or row["status"] not in ("failed","paused","completed","gate"):
                raise InvalidState("请在本轮暂停或失败后再重新拆分。")
            with self.review_lock, self.db.tx():
                project = self.workspace.project(row["project_id"])
                spec = project["review"]["specification"]
                if not spec or spec["status"]!="confirmed":
                    raise InvalidState("当前没有可保留的确认版规格。")
                if row["run_id"]:
                    run = self.db.snapshot(row["run_id"])
                    if run["state"]=="active":
                        self.db.stop_run(run["id"])
                cfg = options({"gates":[*row["settings"]["gates"],"plan"]},row["settings"])
                self._update(key,settings=cfg,stage="plan",status="running",run_id=None,retries={},
                             manual_debug=intent=="replan_manual", gate=None, plan_checkpoint=None,
                             accepted=[g for g in row["accepted"] if g=="spec"],error=None,delivery=None)
            note = "开工后到工作台点「单步派发」，有待审产物时可批准或退回。" if intent=="replan_manual" else ""
            self._message(key,"steward","从已确认的方案重新编排，保留历史运行。新计划会先停在开工前。"+note)
            self._spawn(key)
            return self.public(key)
        if intent in ("revise", "redo"):
            markdown = row["markdown"] + ("\n\n## 用户指定的本轮修改\n\n" + message if intent == "revise" else "")
            if len(markdown.encode("utf-8")) > MAX_INPUT_BYTES:
                raise InvalidReference("新需求超过 1 MB（UTF-8），请简化后重新提交。")
            cfg = options(settings or {}, row["settings"])
            if (cfg["directory"] and row["target"] and row["target"].get("repo")
                    and Path(cfg["directory"]).resolve() == Path(row["target"]["repo"])):
                cfg["directory"] = str(Path(cfg["directory"]) / ("revision-" + uuid4().hex[:8]))
            self._stop_for_revision(key)
            with self.db._lock:
                previous = self._read(key)["messages"]
            new = self.start(markdown, cfg, phase=row["phase"]+1, prior_messages=previous)
            self._message(new["id"], "steward", "已保留上一轮。我会按你的调整重新生成方案、任务和交付。")
            return self.public(new["id"])
        if intent == "pause":
            if row["status"] != "running":
                self._message(key, "steward", "这轮当前没有继续派发。")
                return self.public(key)
            self._update(key, status="pausing")
            self._pause_run(row)
            self._message(key, "steward", "已停止派发新任务；已发出的调用会收尾，之后停在当前检查点。")
        elif intent in ("continue","retry"):
            with self.db.tx():
                row = self._read(key)
                if row["status"] == "gate":
                    row["accepted"] = [*row["accepted"], row["gate"]]
                if row["status"] in ("gate", "paused", "failed"):
                    if row["status"] == "failed":
                        row["retries"] = {}
                    row.update(status="running", gate=None, error=None, activity="正在继续")
                    self._save(row)
                    resume = True
                else:
                    resume = False
            if resume:
                self._message(key, "steward", "我会从当前检查点继续，不会让你逐项填写。")
                self._spawn(key)
            else:
                self._message(key, "steward", "当前没有可恢复的暂停点。需要调整时，可以直接告诉我改哪里。")
        elif intent == "status":
            statuses = dict(running="正在推进", failed="遇到错误", paused="已暂停", pausing="正在收尾",
                            gate="等你选择的确认点", completed="已完成", cancelled="已停止")
            failures = self._evidence(row)
            if failures:
                item = failures[0]
                answer = f"卡在 {item['task']}（{item['title']}）。"
                answer += item["reason"] or "尝试记录没有留下更具体的原因。"
                answer += f"当前已有 {item['attempts']} 次尝试；后续依赖不会带着不合格的产物继续。"
                if "空文件" in item["reason"] or "文件正文" in item["reason"]:
                    answer += "建议说「重拆任务」，先对齐交接文档，再把各功能分开实现。"
            else:
                reason = row["error"] or row["activity"]
                answer = f"目前{statuses.get(row['status'],row['status'])}，在{STAGES[row['stage']]}。{reason}"
            self._message(key, "steward", answer)
        else:
            self._message(key, "steward", "告诉我具体想改哪一条，我会按新要求生成下一轮；也可以直接说暂停、继续或查看进度。")
        return self.public(key)

    def _stop_for_revision(self, key):
        with self.db.tx():
            row = self._read(key)
            if row["status"] not in ("completed","cancelled"):
                row.update(status="cancelled", activity="已切换到下一轮", timeout=0, gate=None)
                self._save(row)
        self._pause_run(row)

    def set_gates(self, key, gates):
        with self.db.tx():
            row = self._read(key)
            settings = options({"gates":gates}, row["settings"])
            row["settings"] = settings
            resume = row["status"] == "gate" and row["gate"] not in settings["gates"]
            if resume:
                row.update(status="running", gate=None)
            self._save(row)
        if resume:
            self._spawn(key)
        return self.public(key)
