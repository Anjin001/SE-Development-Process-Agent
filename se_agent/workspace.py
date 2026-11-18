"""Integrated project workspace using the CLY, HSX, QLM, ZXQ and Hza modules."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
import threading
from uuid import uuid4

from .contracts import DomainError, InvalidReference, InvalidState, NotFound, VersionConflict
from .planner import decompose_tasks, render_plan_markdown
from .synthesis_preview import PreviewError, PreviewStore
from .worker import build_worker


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

    def change(self, project_id, action, payload):
        previous = deepcopy(self.get(project_id))
        try:
            result = super().change(project_id, action, payload)
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
            tasks = decompose_tasks(dict(spec, requirement_items=requirement["items"]))
            verifier = self.database.verifier
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

    def advance(self, run_id, manual=True):
        with self.lock:
            if run_id in self.busy:
                return False
            self.busy.add(run_id)
        try:
            with self.database.tx():
                self.database.expire_leases(run_id)
                if manual:
                    self.database.step(run_id)
                handed = self.database.claim(run_id, "workspace-worker")
            if not handed:
                with self.lock:
                    self.busy.discard(run_id)
                return False
            threading.Thread(target=self._batch, args=(run_id, handed), daemon=True).start()
            return True
        except Exception:
            with self.lock:
                self.busy.discard(run_id)
            raise

    def _execute(self, run_id, request):
        try:
            result = self.worker.execute(request)
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

    def _batch(self, run_id, requests):
        try:
            with ThreadPoolExecutor(max_workers=len(requests)) as pool:
                list(pool.map(lambda request: self._execute(run_id, request), requests))
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
        self.dispatch = Dispatch(database, worker)
        self.actor = "lead-local"

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
        return dict(review=review, planning=plan, run=run)

    def run(self, project_id, run_id):
        snapshot = self.database.snapshot(run_id)
        if snapshot["project_id"] != project_id:
            raise NotFound("运行不属于该项目")
        for task in snapshot["tasks"].values():
            for artifact in task["artifacts"]:
                row = self.database.db.execute(
                    "SELECT content,changed_paths FROM artifacts WHERE run_id=? AND id=? AND version=?",
                    (run_id, artifact["id"], artifact["version"])).fetchone()
                artifact.update(content=row["content"], changed_paths=json.loads(row["changed_paths"]))
        return snapshot

    def action(self, project_id, run_id, action, body):
        snapshot = self.run(project_id, run_id)
        if type(body.get("expected_revision")) is not int or body["expected_revision"] != snapshot["revision"]:
            raise VersionConflict(current_revision=snapshot["revision"])
        task_id = body.get("task_id")
        if action == "advance":
            self.dispatch.advance(run_id)
        elif action in ("pause", "resume"):
            self.database.set_dispatch(run_id, action == "pause")
        elif action == "retry":
            self.database.retry(run_id, task_id)
        elif action == "supply-input":
            self.database.supply_input(run_id, task_id, body.get("values") or {}, self.actor)
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
            for output_id, revision in revisions.items():
                candidate = candidates.get(output_id)
                if not candidate or not isinstance(revision, dict):
                    raise InvalidReference("修订必须对应当前待审核产物")
                if candidate["kind"] == "text":
                    if not isinstance(revision.get("content"), str):
                        raise InvalidReference("文本修订需要正文")
                else:
                    verifier = self.database.verifier
                    if verifier is None:
                        raise InvalidReference("修订代码提交需要配置真实仓库")
                    attempt = self.database._current_attempt(self.database.db, run_id, task_id)
                    required = [ref["commit_sha"] for ref in json.loads(attempt["input_refs"])
                                if ref.get("commit_sha")]
                    verifier.verify(revision.get("commit_sha"), attempt["checkout_commit"], required,
                                    definition["write_paths"])
                    revision["changed_paths"] = verifier.changed_paths(
                        revision["commit_sha"], attempt["checkout_commit"])
            self.database.review(run_id, task_id, body.get("decision"),
                                 body.get("expected_versions") or {}, body.get("revisions") or {},
                                 body.get("note", ""), self.actor)
        else:
            raise InvalidReference("未知运行动作")
        return self.run(project_id, run_id)

    def experiences(self, project_id, terms=None, domain=""):
        self.reviews.get(project_id)
        memory = self.database.memory
        return memory.search(project_id, self.actor, "retrospective", domain, terms) if terms else (
            memory.list_records(project_id, self.actor, "retrospective", kind="experience"))

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

    def _publish_experience(self, project_id, ref, expected_version, note):
        memory = self.database.memory
        record = memory.read(ref["id"], ref["version"], project_id, self.actor, "retrospective")
        if record["kind"] != "experience" or record["version"] != expected_version:
            raise InvalidReference("经验编号或版本无效")
        sources = []
        for source in record["body"]["sources"]:
            item = memory.read(source["id"], source["version"], project_id, self.actor, "retrospective")
            sources.append(source if item["visibility"] == "published" else memory.publish(
                project_id, self.actor, source, source["version"], note))
        updated = memory.save(record["id"], "experience", project_id, record["owner"],
                              dict(record["body"], sources=sources), "project", expected_version)
        return memory.publish(project_id, self.actor, updated, updated["version"], note)
