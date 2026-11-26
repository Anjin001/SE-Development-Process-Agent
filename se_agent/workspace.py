"""Integrated project workspace using the CLY, HSX, QLM, ZXQ and Hza modules."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from math import isfinite
from .model_router import ModelRouter
from .limits import WORKER_LEASE_SECONDS

import json
import logging
import threading
from uuid import uuid4

from .contracts import DomainError, InvalidReference, InvalidState, NotFound, VersionConflict
from .planner import decompose_tasks, render_plan_markdown
from .team_plan import fallback_modules, team_tasks
from .synthesis_preview import PreviewError, PreviewStore
from .worker import Worker, build_worker
from .workspace_experience import Progress, ProjectDelivery


class ProjectReviews(PreviewStore):
    """Persist HSX's complete version history without changing its data model."""

    def __init__(self, database, agent=None):
        super().__init__(agent)
        self.database = database
        database.db.execute("CREATE TABLE IF NOT EXISTS workspace_reviews "
                            "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        for row in database.db.execute("SELECT id, payload FROM workspace_reviews"):
            project = json.loads(row["payload"])
            project["version_sources"] = {int(key): value for key, value in project["version_sources"].items()}
            self.projects[row["id"]] = project

    def _persist(self, project_id):
        with self.database.tx() as db:
            db.execute("INSERT INTO workspace_reviews VALUES (?,?) "
                       "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (project_id, json.dumps(self.projects[project_id], ensure_ascii=False)))

    def create(self, payload):
        result = super().create(payload)
        project_id = result["id"]
        try:
            with self.database.tx():
                self.database.create_project(project_id)
                self.database.memory.bootstrap(project_id, "lead-local")
                self.database.memory.grant(project_id, "lead-local", "experience-author", "member")
                self.database.memory.freeze_baseline(project_id, "lead-local", [
                    {"id": record["id"], "version": record["version"]}
                    for record in self.database.memory.list_records(project_id, "lead-local", "retrospective", kind="experience", limit=1000)
                    if record["visibility"] == "published"])
                self._persist(project_id)
        except Exception:
            self.projects.pop(project_id, None)
            raise
        return result

    def change(self, project_id, action, payload, progress=None, actor="human"):
        previous = deepcopy(self.get(project_id))
        try:
            result = super().change(project_id, action, payload, progress=progress, actor=actor)
            if action != "inspect":
                self._persist(project_id)
            return result
        except Exception:
            self.projects[project_id] = previous
            raise


class ProjectPlans:
    """Use QLM decomposition and ZXQ validation, versioning and scheduling together."""

    def __init__(self, database):
        self.database = database

    def latest(self, project_id):
        row = self.database.db.execute(
            "SELECT id,version FROM plans WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
            (project_id,)).fetchone()
        return self.response(project_id, row["id"], row["version"]) if row else None

    def response(self, project_id, plan_id, version):
        result = self.database.get_plan(project_id, plan_id, version)
        return dict(result, review_id=project_id, task_markdown=render_plan_markdown(result["plan"]))

    def generate(self, review, version):
        spec = review["specification"]
        if type(version) is not int or not spec or spec["status"] != "confirmed" or spec["version"] != version:
            raise PreviewError("INVALID_STATE", "请先确认当前综合方案。")
        project_id = review["id"]
        with self.database.tx() as db:
            latest = self.latest(project_id)
            ref = {"id": spec["id"], "version": version}
            if latest and latest["plan"]["spec_ref"] == ref:
                return latest
            requirement = review["requirement"]
            db.execute("INSERT OR IGNORE INTO requirements VALUES (?,?,?,?)",
                       (project_id, requirement["version"], requirement["markdown"], self.database.now().isoformat()))
            for item in requirement["items"]:
                source = item["source"]
                db.execute("INSERT OR IGNORE INTO requirement_items VALUES (?,?,?,?,?,?,?,?)",
                           (project_id, requirement["version"], item["id"], item["title"], item["text"],
                            source["section"], source["start_line"], source["end_line"]))
            adapted = dict(spec, requirement_version=requirement["version"], requirements=requirement["items"],
                           open_items=[dict(item, requirement_ids=item["affected_requirement_ids"])
                                       for item in spec["open_items"]])
            if not db.execute("SELECT 1 FROM specifications WHERE project_id=? AND version=?",
                              (project_id, version)).fetchone():
                self.database.save_spec(project_id, adapted)
            modules = fallback_modules(requirement["items"])
            # A single input paragraph can describe several independent product behaviours.
            if len(requirement["items"]) == 1 and len(modules)>1:
                tasks = team_tasks(review,modules)
            else:
                tasks = decompose_tasks(dict(spec, requirement_items=requirement["items"]))
            verifier = self.database.verifier_for(project_id)
            baseline = verifier._git("rev-parse", "HEAD").strip() if verifier else None
            plan = self.database.create_plan(project_id, adapted, baseline, 2, tasks)
            return self.response(project_id, plan["id"], plan["version"])

    def save(self, review, expected_version, tasks, max_parallel=None):
        latest = self.latest(review["id"])
        if not latest:
            raise NotFound("请先生成任务分解")
        plan = latest["plan"]
        spec = review["specification"]
        if not spec or spec["status"] != "confirmed" or plan["spec_ref"] != {"id": spec["id"], "version": spec["version"]}:
            raise PreviewError("STALE_SPEC", "规格已变化，请重新交接。")
        if type(expected_version) is not int:
            raise InvalidReference("expected_version 必须为整数")
        originals = {task["id"]: task for task in plan["tasks"]}
        rows = []
        if not isinstance(tasks, list) or not tasks:
            raise InvalidReference("任务不能为空")
        for task in tasks:
            if not isinstance(task, dict):
                raise InvalidReference("任务必须是对象")
            row = dict(task)
            if row.get("id") not in originals:
                refs = row.get("requirement_ids", [])
                row = dict(row, phase=row.get("phase", "development"),
                           decision_ids=[d["id"] for d in spec["decisions"] if set(refs) & set(d["requirement_ids"])],
                           open_item_ids=[o["id"] for o in spec["open_items"] if o["blocking"] and o["resolution"] is None
                                          and set(refs) & set(o["affected_requirement_ids"])])
                path = "docs/custom/" + str(row.get("id", "")).lower() + ".md"
                row.setdefault("write_paths", [path])
                row.setdefault("outputs", [dict(id="OUT-01", kind="text", path=path)])
                row.setdefault("completion_condition", "按关联需求检查任务产物。")
            rows.append(row)
        with self.database.tx():
            saved = self.database.patch_plan(review["id"], plan["id"], expected_version,
                                             max_parallel=max_parallel, tasks=rows)
        return self.response(review["id"], saved["plan"]["id"], saved["plan"]["version"])


class Dispatch:
    """Claim under the database lock, run workers concurrently, submit atomically."""

    def __init__(self, database, worker=None):
        self.database = database
        self.worker = worker or build_worker()
        self.delivery = None
        self.progress = Progress()
        self.busy = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.loop = threading.Thread(target=self._automatic, daemon=True)
        self.loop.start()

    def _automatic(self):
        while not self.stop.wait(0.3):
            try:
                with self.database._lock:
                    rows = list(self.database.db.execute(
                        "SELECT id FROM runs WHERE state='active' AND mode='auto' AND dispatch='active'"))
                for row in rows:
                    self.advance(row["id"], manual=False)
            except (DomainError, RuntimeError):
                if self.stop.is_set():
                    return

    def advance(self, run_id, manual=True, *, single=False, task_id=None):
        with self.lock:
            if run_id in self.busy:
                return False
            self.busy.add(run_id)
        try:
            with self.database.tx():
                self.database.expire_leases(run_id)
                if manual:
                    self.database.step(run_id, single=single, task_id=task_id)
                handed = self.database.claim(run_id, "workspace-worker", lease_seconds=WORKER_LEASE_SECONDS)
                worker = self.worker
                if handed and self.delivery and isinstance(worker, Worker):
                    project_id = self.database.db.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()["project_id"]
                    repo = self.delivery.target(project_id)["repo"]
                    cap = self.delivery.budget(project_id)
                    router = worker.router
                    if cap is not None:
                        config = deepcopy(router.config)
                        config["routing"]["max_cost_usd_per_attempt"] = cap
                        router = ModelRouter(config)
                    if repo or cap is not None:
                        worker = Worker(mode="auto" if repo else worker.mode, repo_root=repo or worker.repo_root,
                                        worktree_root=worker.worktree_root, router=router,
                                        provider=worker.provider, executor=worker.executor)
            if not handed:
                with self.lock:
                    self.busy.discard(run_id)
                return False
            threading.Thread(target=self._batch, args=(run_id, handed, worker), daemon=True).start()
            return True
        except Exception:
            with self.lock:
                self.busy.discard(run_id)
            raise

    def _execute(self, run_id, request, worker):
        try:
            if type(worker) is Worker:
                def milestone(label, seconds=0):
                    self.database.renew(run_id, request["attempt_id"], "workspace-worker", lease_seconds=WORKER_LEASE_SECONDS)
                    self.progress.record(request["attempt_id"], label, seconds)
                result = worker.execute(request, progress=milestone)
            else:
                result = worker.execute(request)
        except Exception as exc:
            result = dict(attempt_id=request["attempt_id"], status="failed", artifacts=[],
                          error=dict(code="EXECUTION_FAILED", message=str(exc), retryable=True, details={}),
                          provider="unknown", model=None, usage=None, checkout_commit=None)
        try:
            self.database.submit(run_id, result, "workspace-worker")
        except Exception as exc:
            if isinstance(exc, DomainError) and exc.code == "STALE_ATTEMPT":
                return
            result.update(status="failed", artifacts=[], error=dict(
                code="OUTPUT_INVALID", message=str(exc), retryable=False, details=getattr(exc, "details", {})))
            try:
                self.database.submit(run_id, result, "workspace-worker")
            except Exception:
                logging.exception("Unable to record worker failure for %s", request["attempt_id"])

    def _batch(self, run_id, requests, worker):
        try:
            with ThreadPoolExecutor(max_workers=len(requests)) as pool:
                list(pool.map(lambda request: self._execute(run_id, request, worker), requests))
        finally:
            with self.lock:
                self.busy.discard(run_id)

    def close(self):
        self.stop.set()
        self.loop.join(timeout=2)


class Workspace:
    def __init__(self, database, reviews, worker=None):
        self.database = database
        self.reviews = reviews
        self.plans = ProjectPlans(database)
        self.delivery = ProjectDelivery(database)
        database.verifier_for = self.delivery.verifier
        self.dispatch = Dispatch(database, worker)
        self.dispatch.delivery = self.delivery
        self.actor = "lead-local"
        database.db.execute("""CREATE TABLE IF NOT EXISTS workspace_profiles (
            project_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(project_id, id))""")

    def projects(self):
        return [{"id": p["id"], "title": p["requirement"]["items"][0]["title"],
                 "requirement": p["requirement"]["id"], "round": p["round"]}
                for p in reversed(list(self.reviews.projects.values()))]

    def project(self, project_id):
        review = self.reviews.snapshot(self.reviews.get(project_id))
        plan = self.plans.latest(project_id)
        runs = list(self.database.db.execute(
            "SELECT id FROM runs WHERE project_id=? ORDER BY rowid DESC", (project_id,)))
        run = self.run(project_id, runs[0]["id"]) if runs else None
        versions = [dict(row) for row in self.database.db.execute(
            "SELECT id, version, spec_id FROM plans WHERE project_id=? ORDER BY rowid DESC", (project_id,))]
        return dict(review=review, planning=plan, plan_versions=versions, run=run, target=self.delivery.target(project_id),
                    model_budget=self.delivery.budget(project_id))

    def save_model_budget(self, project_id, value):
        self.reviews.get(project_id)
        if type(value) not in (int, float) or value < 0:
            raise InvalidReference("费用上限必须为非负数")
        try:
            value = float(value)
        except OverflowError:
            raise InvalidReference("费用上限必须为有限数") from None
        if not isfinite(value):
            raise InvalidReference("费用上限必须为有限数")
        self.delivery.set_budget(project_id, value)
        return {"model_budget": value}

    def worktree_inputs(self, project_id, run_id, task_id):
        with self.database._lock:
            run = self.database.snapshot(run_id)
            if run["project_id"] != project_id or task_id not in run["tasks"]:
                raise NotFound("任务不属于该项目或运行")
            attempt = self.database.db.execute(
                "SELECT checkout_commit, input_refs FROM attempts WHERE run_id=? AND task_id=? "
                "ORDER BY number DESC LIMIT 1", (run_id, task_id)).fetchone()
            verifier = self.database.verifier_for(project_id)
        if not attempt:
            return {"checkout": None, "items": []}
        checkout = attempt["checkout_commit"]
        commits = dict.fromkeys(ref["commit_sha"] for ref in json.loads(attempt["input_refs"])
                                if ref.get("commit_sha"))
        readable = bool(verifier and checkout and verifier.exists(checkout))
        return {"checkout": checkout, "items": [
            {"commit": commit, "included": verifier.is_ancestor(commit, checkout)
             if readable and verifier.exists(commit) else None} for commit in commits
        ]}

    def review_patch(self, project_id, run_id, task_id, output_id=None, against="checkout"):
        if against not in ("checkout", "previous"):
            raise InvalidReference("补丁对比基准无效")
        with self.database._lock:
            task = self.run(project_id, run_id)["tasks"].get(task_id)
            if task is None:
                raise NotFound("任务不存在")
            artifacts = [item for item in task["artifacts"] if item["commit_sha"]]
            if output_id is not None:
                artifacts = [item for item in artifacts if item["output_id"] == output_id]
            artifact = artifacts[-1] if artifacts else None
            attempt = artifact and next((item for item in task["attempts"] if item["id"] == artifact["attempt_id"]), None)
            verifier = self.database.verifier_for(project_id)
        if not verifier or not artifact or not attempt or not attempt["checkout_commit"]:
            return {"patch": "", "truncated": False}
        base, paths = attempt["checkout_commit"], artifact["changed_paths"]
        if against == "previous":
            previous = next((item for item in reversed(artifacts[:-1])
                             if item["output_id"] == artifact["output_id"]), None)
            if not previous:
                return {"patch": "", "truncated": False}
            base = previous["commit_sha"]
            paths = list(dict.fromkeys(previous["changed_paths"] + paths))
        return verifier.review_patch(base, artifact["commit_sha"], paths)

    def run(self, project_id, run_id):
        snapshot = self.database.snapshot(run_id)
        if snapshot["project_id"] != project_id:
            raise NotFound("运行不属于该项目")
        for task in snapshot["tasks"].values():
            attempts = task["attempts"]
            if attempts and task["state"] == "running":
                task["activity"] = self.dispatch.progress.read(attempts[-1]["id"])
            for artifact in task["artifacts"]:
                row = self.database.db.execute(
                    "SELECT content,changed_paths,attempt_id FROM artifacts WHERE run_id=? AND id=? AND version=?",
                    (run_id, artifact["id"], artifact["version"])).fetchone()
                artifact.update(content=row["content"],changed_paths=json.loads(row["changed_paths"]),
                                attempt_id=row["attempt_id"])
        snapshot["rework_edges"] = [
            {"from": payload["verification_task_id"], "to": row["task_id"], "note": payload.get("note", "")}
            for row in self.database.db.execute(
                "SELECT task_id,payload FROM events WHERE run_id=? AND type='artifact.rejected' ORDER BY seq DESC LIMIT 30",
                (run_id,))
            for payload in [json.loads(row["payload"])] if payload.get("verification_task_id")
        ]
        return snapshot

    def action(self, project_id, run_id, action, body):
        snapshot = self.run(project_id, run_id)
        if type(body.get("expected_revision")) is not int or body["expected_revision"] != snapshot["revision"]:
            raise VersionConflict(current_revision=snapshot["revision"])
        task_id = body.get("task_id")
        if snapshot["state"] != "active":
            raise InvalidState("这次运行已结束。")
        if action == "stop":
            self.database.stop_run(run_id)
        elif action == "advance":
            dispatched = self.dispatch.advance(run_id, single=body.get("single") is True, task_id=task_id)
            if task_id is not None and not dispatched:
                raise InvalidState("当前任务无法派发，请刷新状态后再试。")
        elif action in ("pause", "resume"):
            self.database.set_dispatch(run_id, action == "pause")
        elif action == "retry":
            self.database.retry(run_id, task_id)
        elif action == "verification-return":
            self.database.return_from_verification(run_id, task_id, body.get("target_task_id"),
                                                   body.get("note", ""), self.actor)
        elif action == "supply-input":
            self.database.supply_input(run_id, task_id, body.get("values") or {}, self.actor)
        elif action in ("steward-answer", "steward-apply"):
            item_id = body.get("item_id")
            answer = body.get("answer")
            if action == "steward-answer" and (not isinstance(answer, str) or not answer.strip() or len(answer) > 1000):
                raise InvalidReference("请用一句话填写答案。")
            tasks = [task["task_id"] for task in snapshot["tasks"].values()
                     if item_id in task["missing_input_ids"]]
            if not tasks:
                raise InvalidReference("这个问题已处理，或不影响当前运行。")
            question = next((n for n in self.steward_questions(project_id, run_id) if n["id"] == item_id), None)
            if action == "steward-apply":
                answer = question["default"] if question else None
                if not answer:
                    raise InvalidState("这项涉及产品行为，需要人工决定。")
            if question and question["note"]:
                answer = answer.strip() + "\n主理人技术默认：" + question["note"]
            for id_ in tasks:
                self.database.supply_input(run_id, id_, {item_id: answer.strip()},
                                           "steward" if action == "steward-apply" else self.actor)
        elif action == "steward-defaults":
            for note in self.steward_questions(project_id, run_id):
                if note["default"]:
                    for id_ in note["tasks"]:
                        self.database.supply_input(run_id, id_, {note["id"]: note["default"]}, "steward")
        elif action == "review":
            revisions = body.get("revisions") or {}
            task = snapshot["tasks"].get(task_id)
            if not task or not isinstance(revisions, dict):
                raise InvalidReference("审核任务或修订格式无效")
            candidates = {artifact["output_id"]: artifact for artifact in task["artifacts"]
                          if artifact["status"] == "draft"}
            plan_tasks = self.database.plan_tasks(project_id, snapshot["plan_ref"]["id"],
                                                  snapshot["plan_ref"]["version"])
            definition = next(row for row in plan_tasks if row["id"] == task_id)
            review_note = body.get("note", "")
            if (definition["phase"] == "verification" and body.get("decision") in ("approve", "revise")
                    and (not isinstance(review_note, str) or not review_note.strip())):
                raise InvalidReference("请记录实际验证的步骤和结果，再批准验收", field="note")
            for output_id, revision in revisions.items():
                candidate = candidates.get(output_id)
                if not candidate or not isinstance(revision, dict):
                    raise InvalidReference("修订必须对应当前待审核产物")
                if candidate["kind"] == "text":
                    if not isinstance(revision.get("content"), str):
                        raise InvalidReference("文本修订需要正文")
                else:
                    verifier = self.database.verifier_for(project_id)
                    attempt = self.database._current_attempt(self.database.db, run_id, task_id)
                    if verifier is None or not attempt["checkout_commit"]:
                        raise InvalidReference("这次任务没有真实代码提交。")
                    required = [ref["commit_sha"] for ref in json.loads(attempt["input_refs"])
                                if ref.get("commit_sha")]
                    verifier.verify(revision.get("commit_sha"), attempt["checkout_commit"], required,
                                    definition["write_paths"])
                    revision["changed_paths"] = verifier.changed_paths(
                        revision["commit_sha"], attempt["checkout_commit"])
            self.database.review(run_id, task_id, body.get("decision"),
                                 body.get("expected_versions") or {}, body.get("revisions") or {},
                                 review_note, self.actor)
        else:
            raise InvalidReference("未知运行动作")
        return self.run(project_id, run_id)

    def configure_target(self, project_id, path, real=False):
        target = self.delivery.configure(project_id, path, real)
        if real:
            latest = self.plans.latest(project_id)
            if latest and not latest["plan"]["base_commit"]:
                plan = latest["plan"]
                baseline = self.delivery.verifier(project_id)._git("rev-parse", "HEAD").strip()
                self.database.create_plan(project_id, plan["spec_ref"], baseline,
                                          plan["max_parallel"], plan["tasks"], plan_id=plan["id"])
        return self.project(project_id)

    def steward_questions(self, project_id, run_id):
        run = self.run(project_id, run_id)
        plan = self.database.get_plan(project_id, run["plan_ref"]["id"], run["plan_ref"]["version"])["plan"]
        rows = self.database.db.execute(
            "SELECT id, question FROM spec_open_items WHERE project_id=? AND spec_version=?",
            (project_id, plan["spec_ref"]["version"]))
        questions = {row["id"]: row["question"] for row in rows}
        spec = self.reviews.version(project_id, plan["spec_ref"]["id"], plan["spec_ref"]["version"])
        items = {item["id"]: item for item in spec.get("open_items", [])}
        requirement = self.reviews.get(project_id)["requirement"]
        from .workspace_experience import steward_question
        missing = {}
        for task in run["tasks"].values():
            for id_ in task["missing_input_ids"]:
                missing.setdefault(id_, []).append(task["task_id"])
        return [steward_question(id_, items.get(id_, {"question": questions.get(id_, "请补充此项。")}),
                                 task_ids, requirement) for id_, task_ids in sorted(missing.items())]

    def profiles(self, project_id):
        self.reviews.get(project_id)
        rows = self.database.db.execute(
            "SELECT payload FROM workspace_profiles WHERE project_id=? ORDER BY rowid", (project_id,))
        return [json.loads(row["payload"]) for row in rows]

    def save_profile(self, project_id, body):
        project = self.reviews.get(project_id)
        with self.database.tx() as db:
            profiles = self.profiles(project_id)
            if "id" not in body:
                title = project["requirement"]["items"][0]["title"]
                profile = dict(id=f"AG-{len(profiles) + 1:02}", name="安全与权限审计专家", role="交叉评审员",
                               domain="RBAC 权限隔离、接口鉴权", focus="检查管理员接口越权与跨组数据泄露",
                               history=f"新增数字人角色，已绑定「{title}」基线")
                db.execute("INSERT INTO workspace_profiles VALUES(?,?,?)",
                           (project_id, profile["id"], json.dumps(profile, ensure_ascii=False)))
                return profile
            profile = next((item for item in profiles if item["id"] == body["id"]), None)
            if profile is None:
                raise NotFound("数字人档案不存在")
            field, value = body.get("field"), body.get("value")
            if field not in ("name", "domain", "focus") or not isinstance(value, str) or len(value) > 10000:
                raise InvalidReference("数字人档案字段或内容不合法")
            profile[field] = value
            db.execute("UPDATE workspace_profiles SET payload=? WHERE project_id=? AND id=?",
                       (json.dumps(profile, ensure_ascii=False), project_id, profile["id"]))
            return profile

    def experiences(self, project_id, terms=None, domain=""):
        self.reviews.get(project_id)
        memory = self.database.memory
        return memory.search(project_id, self.actor, "retrospective", domain, terms) if terms else (
            memory.list_records(project_id, self.actor, "retrospective", kind="experience"))

    def experience_baseline(self, project_id):
        self.reviews.get(project_id)
        row = self.database.db.execute("SELECT refs FROM dm_baselines WHERE project=?", (project_id,)).fetchone()
        return json.loads(row["refs"]) if row else None

    def refresh_baseline(self, project_id):
        self.reviews.get(project_id)
        memory = self.database.memory
        refs = [{"id": row["id"], "version": row["version"]}
                for row in memory.list_records(project_id, self.actor, "retrospective", kind="experience", limit=1000)
                if row["visibility"] == "published"]
        self.database.db.execute("DELETE FROM dm_baselines WHERE project=?", (project_id,))
        memory.freeze_baseline(project_id, self.actor, refs)
        return {"refs": refs}

    def record_experience(self, project_id, body):
        with self.database.tx():
            return self._record_experience(project_id, body)

    def _record_experience(self, project_id, body):
        self.reviews.get(project_id)
        for field in ("problem", "cause", "correction", "domain", "trigger", "pass_condition", "evidence"):
            if not isinstance(body.get(field), str) or not body[field].strip():
                raise InvalidReference("经验需要具体的问题、原因、改正方法和领域", field=field)
        tags = body.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags):
            raise InvalidReference("关键词应为文本列表")
        memory = self.database.memory
        source = memory.save("EVIDENCE-" + uuid4().hex[:12], "artifact", project_id,
                             "experience-author", dict(evidence=body["evidence"]), visibility="project")
        return memory.save(
            "EXP-" + uuid4().hex[:12], "experience", project_id, "experience-author",
            dict(body, tags=tags, evidence_state="pending", sources=[source], source_project=project_id),
            visibility="project")

    def publish_experience(self, project_id, ref, expected_version, note):
        with self.database.tx():
            return self._publish_experience(project_id, ref, expected_version, note)

    def publish_cards(self, project_id, cards):
        if not isinstance(cards, list) or not 1 <= len(cards) <= 100:
            raise InvalidReference("请选择 1 至 100 张经验卡")
        with self.database.tx():
            references = []
            for card in cards:
                if not isinstance(card, dict):
                    raise InvalidReference("经验卡应为对象")
                content = card.get("content")
                if not isinstance(content, dict):
                    raise InvalidReference("经验卡内容应为对象")
                fields = ("problem", "cause", "correction", "domain", "trigger", "pass_condition", "evidence")
                details = {field: content.get(field) for field in fields}
                if any(not isinstance(value, str) or not value.strip() for value in details.values()):
                    raise InvalidReference("请填写完整经验并提供验证记录")
                tags = content.get("tags", [])
                counter = content.get("counter_example", "")
                level = content.get("evidence_state")
                if (not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags)
                        or not isinstance(counter, str) or level not in ("walkthrough", "pilot", "production")):
                    raise InvalidReference("经验卡关键词、反例或证据等级不合法")
                details.update(tags=tags, counter_example=counter, evidence_state=level)
                ref = card.get("ref")
                if ref is None:
                    ref = self._record_experience(project_id, details)
                references.append(self._publish_experience(
                    project_id, ref, ref["version"], "经项目负责人核对后发布", details))
            return references

    def _publish_experience(self, project_id, ref, expected_version, note, changes=None):
        memory = self.database.memory
        record = memory.read(ref["id"], ref["version"], project_id, self.actor, "retrospective")
        if record["kind"] != "experience" or record["project"] != project_id or record["version"] != expected_version:
            raise InvalidReference("经验编号或版本无效")
        body = dict(record["body"], **(changes or {}))
        source_refs = list(record["body"]["sources"])
        if changes and body["evidence"] != record["body"].get("evidence"):
            source_refs.append(memory.save("EVIDENCE-" + uuid4().hex[:12], "artifact", project_id,
                                           record["owner"], dict(evidence=body["evidence"]), visibility="project"))
        sources = []
        for source in source_refs:
            item = memory.read(source["id"], source["version"], project_id, self.actor, "retrospective")
            sources.append(source if item["visibility"] == "published" else memory.publish(
                project_id, self.actor, source, source["version"], note))
        updated = memory.save(record["id"], "experience", project_id, record["owner"],
                              dict(body, sources=sources), "project", expected_version)
        return memory.publish(project_id, self.actor, updated, updated["version"], note)
