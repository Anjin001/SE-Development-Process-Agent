"""SQLite persistence for v2 plans and runs (docs/接口契约-v2.md §3-§5).

One database serves both modules this member owns. This slice implements the
run lifecycle: the eight task states, lease-based claiming, worker reports,
human review, events and revisions. The memory tables ship as schema; their
service is the next slice.

Concurrency rules that matter here:
  * every externally visible state change bumps ``runs.revision`` once per
    transaction and appends its event in the same transaction;
  * renewing a lease touches only ``attempts.lease_until``;
  * an idempotent replay changes nothing;
  * tasks whose ``write_paths`` overlap never run at the same time, and the
    comparison is by path segment so ``app`` does not collide with ``app2``.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import (Conflict, DomainError, InvalidOutput, InvalidReference, InvalidState,
                        NotFound, StaleAttempt, VersionConflict)
from .plan_rules import overlaps, validate_plan
from .schedule import schedule_plan

SCHEMA = Path(__file__).with_name("schema.sql")

WAITING, READY, RUNNING = "waiting", "ready", "running"
REVIEW, DONE, FAILED = "review", "done", "failed"
INPUT_REQUIRED, BLOCKED = "input_required", "blocked"
OPEN_STATES = (WAITING, READY, BLOCKED, INPUT_REQUIRED)

_MISSING = object()


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(moment):
    return moment.isoformat()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(text, default):
    return default if text in (None, "") else json.loads(text)


class Store:
    def __init__(self, path=":memory:", now=None, verifier=None):
        self.now = now or utcnow
        self.verifier = verifier
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), isolation_level=None,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA.read_text(encoding="utf-8"))

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    @contextmanager
    def tx(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    # ---------- projects, specs ----------

    def create_project(self, project_id, stage="intake"):
        with self.tx() as db:
            db.execute("INSERT INTO projects(id, stage, created_at) VALUES (?,?,?)",
                       (project_id, stage, _stamp(self.now())))
        return {"id": project_id, "stage": stage}

    def save_spec(self, project_id, spec):
        """Persist a confirmed specification so plans can bind it."""
        with self.tx() as db:
            db.execute(
                "INSERT INTO specifications(project_id, version, id, status,"
                " requirement_version, selection, markdown, confirmed_by, confirmed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (project_id, spec["version"], spec["id"], spec.get("status", "confirmed"),
                 spec.get("requirement_version"), spec.get("selection"),
                 spec.get("markdown", ""), spec.get("confirmed_by"), spec.get("confirmed_at")))
            for decision in spec.get("decisions", []):
                db.execute(
                    "INSERT INTO spec_decisions(project_id, spec_version, id, scope, choice,"
                    " reason, requirement_ids, draft_ids) VALUES (?,?,?,?,?,?,?,?)",
                    (project_id, spec["version"], decision["id"], decision.get("scope"),
                     decision.get("choice", ""), decision.get("reason"),
                     _dump(decision.get("requirement_ids", [])),
                     _dump(decision.get("draft_ids", []))))
            for item in spec.get("open_items", []):
                db.execute(
                    "INSERT INTO spec_open_items(project_id, spec_version, id, question,"
                    " blocking, requirement_ids, resolution) VALUES (?,?,?,?,?,?,?)",
                    (project_id, spec["version"], item["id"], item.get("question", ""),
                     1 if item.get("blocking", True) else 0,
                     _dump(item.get("requirement_ids", [])), item.get("resolution")))
        return spec

    def get_spec(self, project_id, version):
        row = self.db.execute("SELECT * FROM specifications WHERE project_id=? AND version=?",
                              (project_id, version)).fetchone()
        if row is None:
            raise NotFound("规格版本不存在", field="spec_ref", version=version)
        if row["status"] != "confirmed":
            raise InvalidState("规格尚未确认", version=version)
        return {"id": row["id"], "version": row["version"], "status": row["status"],
                "selection": row["selection"], "markdown": row["markdown"]}

    def open_items(self, project_id, spec_version, blocking_only=True):
        rows = self.db.execute(
            "SELECT id, blocking, resolution FROM spec_open_items"
            " WHERE project_id=? AND spec_version=?", (project_id, spec_version)).fetchall()
        return [row["id"] for row in rows
                if (not blocking_only or row["blocking"]) and not row["resolution"]]

    # ---------- plans ----------

    def create_plan(self, project_id, spec, base_commit, max_parallel, tasks,
                    plan_id=None):
        version = self._next_plan_version(project_id, plan_id)
        plan = {"id": plan_id or self._plan_id(project_id), "version": version,
                "spec_ref": {"id": spec["id"], "version": spec["version"]},
                "base_commit": base_commit, "max_parallel": max_parallel,
                "tasks": tasks}
        validate_plan(plan, self._spec_payload(project_id, spec))
        with self.tx() as db:
            self._insert_plan(db, project_id, plan)
        return plan

    def _insert_plan(self, db, project_id, plan):
        db.execute(
            "INSERT INTO plans(project_id, id, version, spec_id, spec_version,"
            " base_commit, max_parallel, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (project_id, plan["id"], plan["version"], plan["spec_ref"]["id"],
             plan["spec_ref"]["version"], plan["base_commit"], plan["max_parallel"],
             _stamp(self.now())))
        for position, task in enumerate(plan["tasks"]):
            db.execute(
                "INSERT INTO plan_tasks(project_id, plan_id, plan_version, position,"
                " id, title, phase, role, depends_on, write_paths, outputs,"
                " requirement_ids, decision_ids, open_item_ids, estimate_seconds,"
                " review_required, completion_condition)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, plan["id"], plan["version"], position, task["id"],
                 task["title"], task["phase"], task["role"],
                 _dump(task.get("depends_on", [])), _dump(task.get("write_paths", [])),
                 _dump(task.get("outputs", [])), _dump(task.get("requirement_ids", [])),
                 _dump(task.get("decision_ids", [])), _dump(task.get("open_item_ids", [])),
                 task.get("estimate_seconds"), 1 if task.get("review_required") else 0,
                 task["completion_condition"]))

    def get_plan(self, project_id, plan_id, version):
        row = self.db.execute("SELECT * FROM plans WHERE project_id=? AND id=? AND version=?",
                              (project_id, plan_id, version)).fetchone()
        if row is None:
            raise NotFound("计划版本不存在", field="plan_ref",
                           plan_id=plan_id, version=version)
        plan = {"id": row["id"], "version": row["version"],
                "spec_ref": {"id": row["spec_id"], "version": row["spec_version"]},
                "base_commit": row["base_commit"], "max_parallel": row["max_parallel"],
                "tasks": self.plan_tasks(project_id, plan_id, version)}
        return {"plan": plan, "schedule": schedule_plan(plan)}

    def latest_plan_version(self, project_id, plan_id):
        row = self.db.execute("SELECT MAX(version) AS version FROM plans"
                              " WHERE project_id=? AND id=?", (project_id, plan_id)).fetchone()
        if row["version"] is None:
            raise NotFound("计划不存在", field="plan_id", plan_id=plan_id)
        return row["version"]

    def patch_plan(self, project_id, plan_id, expected_version, max_parallel=None,
                   tasks=None):
        latest = self.latest_plan_version(project_id, plan_id)
        if expected_version != latest:
            raise VersionConflict(current_version=latest)
        current = self.get_plan(project_id, plan_id, latest)["plan"]
        updated = {**current, "version": latest + 1,
                   "max_parallel": current["max_parallel"] if max_parallel is None
                   else max_parallel,
                   "tasks": current["tasks"] if tasks is None else tasks}
        spec = self._spec_payload(project_id, dict(current["spec_ref"]))
        validate_plan(updated, spec)
        with self.tx() as db:
            self._insert_plan(db, project_id, updated)
        return self.get_plan(project_id, plan_id, updated["version"])

    def _plan_id(self, project_id):
        total = self.db.execute("SELECT COUNT(DISTINCT id) AS n FROM plans WHERE project_id=?",
                                (project_id,)).fetchone()["n"]
        return "PLAN-%02d" % (total + 1)

    def _next_plan_version(self, project_id, plan_id):
        if plan_id is None:
            return 1
        row = self.db.execute("SELECT COALESCE(MAX(version), 0) AS last FROM plans"
                              " WHERE project_id=? AND id=?", (project_id, plan_id)).fetchone()
        return row["last"] + 1

    def _spec_payload(self, project_id, spec):
        """The bound specification as validate_plan sees it."""
        if "decisions" in spec:
            return spec
        version = spec["version"]
        return {"id": spec["id"], "version": version,
                "decisions": [dict(row) for row in self.db.execute(
                    "SELECT id FROM spec_decisions WHERE project_id=? AND spec_version=?",
                    (project_id, version))],
                "open_items": [dict(row) for row in self.db.execute(
                    "SELECT id, blocking FROM spec_open_items"
                    " WHERE project_id=? AND spec_version=?", (project_id, version))]}

    def plan_tasks(self, project_id, plan_id, version):
        rows = self.db.execute(
            "SELECT * FROM plan_tasks WHERE project_id=? AND plan_id=? AND plan_version=?"
            " ORDER BY position", (project_id, plan_id, version)).fetchall()
        return [{"id": row["id"], "title": row["title"], "phase": row["phase"],
                 "role": row["role"], "depends_on": _load(row["depends_on"], []),
                 "write_paths": _load(row["write_paths"], []),
                 "outputs": _load(row["outputs"], []),
                 "requirement_ids": _load(row["requirement_ids"], []),
                 "decision_ids": _load(row["decision_ids"], []),
                 "open_item_ids": _load(row["open_item_ids"], []),
                 "estimate_seconds": row["estimate_seconds"],
                 "review_required": bool(row["review_required"]),
                 "completion_condition": row["completion_condition"]} for row in rows]

    # ---------- runs ----------

    def create_run(self, project_id, plan_id, plan_version, mode="manual"):
        plan = self.db.execute("SELECT * FROM plans WHERE project_id=? AND id=? AND version=?",
                               (project_id, plan_id, plan_version)).fetchone()
        if plan is None:
            raise NotFound("计划版本不存在", field="plan_ref",
                           plan_id=plan_id, version=plan_version)
        active = self.db.execute("SELECT id FROM runs WHERE project_id=? AND state='active'",
                                 (project_id,)).fetchone()
        if active:
            raise InvalidState("同一项目已有活动运行", run_id=active["id"])
        tasks = self.plan_tasks(project_id, plan_id, plan_version)
        pending = set(self.open_items(project_id, plan["spec_version"]))
        run_id = self._run_id(project_id)
        now = _stamp(self.now())
        with self.tx() as db:
            db.execute(
                "INSERT INTO runs(id, project_id, plan_id, plan_version, mode, dispatch,"
                " state, revision, started_at, latest_event_seq) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, plan_id, plan_version, mode, "active", "active",
                 1, now, 0))
            for task in tasks:
                missing = [item for item in task["open_item_ids"] if item in pending]
                db.execute(
                    "INSERT INTO run_tasks(run_id, task_id, state, blockers, missing_input_ids)"
                    " VALUES (?,?,?,?,?)", (run_id, task["id"], WAITING, "[]", _dump(missing)))
            self._emit(db, run_id, "run.started", payload={"plan_ref": {
                "id": plan_id, "version": plan_version}})
            self._recompute(db, run_id)
        return self.snapshot(run_id)

    def _run_id(self, project_id):
        total = self.db.execute("SELECT COUNT(*) AS n FROM runs WHERE project_id=?",
                                (project_id,)).fetchone()["n"]
        return "RUN-%02d" % (total + 1)

    # ---------- state machine ----------

    def _task_map(self, db, run_id):
        run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise NotFound("运行不存在", run_id=run_id)
        return run, {row["task_id"]: row for row in db.execute(
            "SELECT * FROM run_tasks WHERE run_id=?", (run_id,))}

    def _recompute(self, db, run_id):
        """Settle waiting/ready/blocked/input_required from predecessors."""
        run, rows = self._task_map(db, run_id)
        tasks = {task["id"]: task for task in
                 self.plan_tasks(run["project_id"], run["plan_id"], run["plan_version"])}
        changed = True
        while changed:
            changed = False
            for task_id, row in rows.items():
                if row["state"] not in OPEN_STATES:
                    continue
                state, blockers = self._settle(rows, tasks[task_id])
                missing = _load(row["missing_input_ids"], [])
                if state == READY and missing:
                    state, blockers = INPUT_REQUIRED, [
                        {"code": "MISSING_INPUT", "ref_id": item,
                         "message": "等待补充输入"} for item in missing]
                if state != row["state"] or _dump(blockers) != _dump(_load(row["blockers"], [])):
                    db.execute("UPDATE run_tasks SET state=?, blockers=? WHERE run_id=? AND task_id=?",
                               (state, _dump(blockers), run_id, task_id))
                    row = dict(row, state=state, blockers=_dump(blockers))
                    rows[task_id] = row
                    changed = True
        done = [row for row in rows.values() if row["state"] == DONE]
        if len(done) == len(rows):
            db.execute("UPDATE runs SET state='completed', ended_at=? WHERE id=?",
                       (_stamp(self.now()), run_id))
        self._refresh_findings(db, run_id, rows, tasks)

    def _refresh_findings(self, db, run_id, rows, tasks):
        """Rebuild the supervisor prompts; they are derived, never hand-edited."""
        found = []
        for task_id, row in rows.items():
            downstream = self._descendants(tasks, task_id)
            if row["state"] == FAILED:
                found.append(("TASK_FAILED", task_id, "execution", "error",
                              f"{task_id} 失败，下游等待重试", [f"task:{task_id}"],
                              [task_id] + downstream, "查看尝试记录后重试或退回重做"))
            elif row["state"] == BLOCKED:
                found.append(("TASK_BLOCKED", task_id, "execution", "warning",
                              f"{task_id} 因上游失败受阻", [f"task:{task_id}"],
                              [task_id], "先处理失败的上游任务"))
            elif row["state"] == REVIEW:
                found.append(("REVIEW_WAITING", task_id, "execution", "info",
                              f"{task_id} 等待人工审核，下游暂停", [f"task:{task_id}"],
                              [task_id] + downstream, "审阅产物并放行"))
            elif row["state"] == INPUT_REQUIRED:
                found.append(("INPUT_REQUIRED", task_id, "execution", "warning",
                              f"{task_id} 缺少输入：{'、'.join(_load(row['missing_input_ids'], []))}",
                              [f"task:{task_id}"], [task_id] + downstream,
                              "在任务详情补充缺项"))
        db.execute("DELETE FROM findings WHERE run_id=?", (run_id,))
        for code, ref_id, stage, severity, summary, evidence, affected, action in found:
            db.execute(
                "INSERT INTO findings(run_id, code, ref_id, stage, severity, summary,"
                " evidence_refs, affected_task_ids, suggested_action)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, code, ref_id, stage, severity, summary, _dump(evidence),
                 _dump(affected), action))

    def _descendants(self, tasks, task_id):
        children = {}
        for task in tasks.values():
            for dep in task["depends_on"]:
                children.setdefault(dep, []).append(task["id"])
        seen, queue = [], [task_id]
        while queue:
            current = queue.pop(0)
            for child in children.get(current, []):
                if child not in seen:
                    seen.append(child)
                    queue.append(child)
        return sorted(seen)

    def _settle(self, rows, task):
        """Failure first, then unfinished predecessors, then input."""
        deps = task["depends_on"]
        broken = [dep for dep in deps if rows[dep]["state"] in (FAILED, BLOCKED)]
        if broken:
            return BLOCKED, [{"code": "UPSTREAM_FAILED", "ref_id": broken[0],
                              "message": "上游任务失败"}]
        reviewing = [dep for dep in deps if rows[dep]["state"] == REVIEW]
        if reviewing:
            return WAITING, [{"code": "UPSTREAM_REVIEW", "ref_id": reviewing[0],
                              "message": "等待上游审核放行"}]
        unfinished = [dep for dep in deps
                      if rows[dep]["state"] in (WAITING, READY, RUNNING, INPUT_REQUIRED)]
        if unfinished:
            code = "UPSTREAM_RUNNING" if any(rows[dep]["state"] in (READY, RUNNING)
                                             for dep in unfinished) else "UPSTREAM_PENDING"
            return WAITING, [{"code": code, "ref_id": unfinished[0],
                              "message": "等待上游任务"}]
        return READY, []

    # ---------- dispatch ----------

    def step(self, run_id):
        """Manual mode: fix this batch as the queue that claim() consumes."""
        with self.tx() as db:
            run, rows = self._task_map(db, run_id)
            if run["mode"] != "manual":
                raise InvalidState("自动模式不需要单步", run_id=run_id)
            if run["state"] != "active" or run["dispatch"] != "active":
                raise InvalidState("当前不可派发", run_id=run_id)
            pending = [row["task_id"] for row in db.execute(
                "SELECT task_id FROM dispatch_queue WHERE run_id=? ORDER BY position",
                (run_id,))]
            if pending:
                raise InvalidState("上一批名单尚未领取完", pending=pending)
            tasks = {task["id"]: task for task in self.plan_tasks(
                run["project_id"], run["plan_id"], run["plan_version"])}
            batch = self._admit(db, run, rows, tasks,
                                sorted(t for t, row in rows.items() if row["state"] == READY))
            if not batch:
                raise InvalidState("没有可派发的任务", run_id=run_id)
            for position, task_id in enumerate(batch):
                db.execute("INSERT INTO dispatch_queue(run_id, task_id, position)"
                           " VALUES (?,?,?)", (run_id, task_id, position))
            return batch

    def _admit(self, db, run, rows, tasks, order):
        """Slots and write-path contention decide which candidates may start."""
        plan = db.execute("SELECT max_parallel FROM plans WHERE project_id=? AND id=?"
                          " AND version=?", (run["project_id"], run["plan_id"],
                                             run["plan_version"])).fetchone()
        running = [task_id for task_id, row in rows.items() if row["state"] == RUNNING]
        slots = plan["max_parallel"] - len(running)
        if slots <= 0:
            return []
        busy = [path for task_id in running for path in tasks[task_id]["write_paths"]]
        taken, batch = [], []
        for task_id in order:
            if len(batch) >= slots:
                break
            if rows[task_id]["state"] != READY:
                continue
            paths = tasks[task_id]["write_paths"]
            if any(overlaps(left, right) for left in paths for right in busy + taken):
                continue
            taken.extend(paths)
            batch.append(task_id)
        return batch

    def claim(self, run_id, worker_id, lease_seconds=300, limit=None):
        """Hand out ready tasks: one attempt each, respecting slots and paths."""
        with self.tx() as db:
            run, rows = self._task_map(db, run_id)
            if run["state"] != "active" or run["dispatch"] != "active":
                return []
            plan = db.execute("SELECT * FROM plans WHERE project_id=? AND id=?"
                              " AND version=?", (run["project_id"], run["plan_id"],
                                                 run["plan_version"])).fetchone()
            tasks = {task["id"]: task for task in self.plan_tasks(
                run["project_id"], run["plan_id"], run["plan_version"])}
            queued = [row["task_id"] for row in db.execute(
                "SELECT task_id FROM dispatch_queue WHERE run_id=? ORDER BY position",
                (run_id,))]
            if run["mode"] == "manual" and not queued:
                return []                       # manual dispatch only follows step()
            order = queued or sorted(task_id for task_id, row in rows.items()
                                     if row["state"] == READY)
            batch = self._admit(db, run, rows, tasks, order)
            if limit is not None:
                batch = batch[:limit]
            for task_id in queued:
                if rows[task_id]["state"] != READY:      # stale entry, drop it
                    db.execute("DELETE FROM dispatch_queue WHERE run_id=? AND task_id=?",
                               (run_id, task_id))
            for task_id in batch:
                db.execute("DELETE FROM dispatch_queue WHERE run_id=? AND task_id=?",
                           (run_id, task_id))
            if not batch:
                return []
            now = self.now()
            requests = []
            for task_id in batch:
                number = self._next_attempt_number(db, run_id, task_id)
                attempt_id = f"{task_id}.{number}"
                inputs = self._approved_inputs(db, run_id, tasks[task_id]["depends_on"])
                db.execute(
                    "INSERT INTO attempts(id, run_id, task_id, number, state, worker_id,"
                    " lease_until, created_at, started_at, input_refs)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (attempt_id, run_id, task_id, number, "running", worker_id,
                     _stamp(now + timedelta(seconds=lease_seconds)),
                     _stamp(now), _stamp(now), _dump(inputs)))
                db.execute("UPDATE run_tasks SET state=?, blockers='[]' WHERE run_id=? AND task_id=?",
                           (RUNNING, run_id, task_id))
                self._emit(db, run_id, "task.dispatched", task_id=task_id,
                           attempt_id=attempt_id)
                requests.append(self._worker_request(db, run, plan, tasks[task_id],
                                                    attempt_id, inputs))
            self._bump(db, run_id)
            return requests

    def _worker_request(self, db, run, plan, task, attempt_id, inputs):
        """The WorkerRequest shape from docs/接口契约-v2.md §5."""
        supplied = {row["item_id"]: row["value"] for row in db.execute(
            "SELECT item_id, value FROM input_submissions WHERE run_id=? AND task_id=?",
            (run["id"], task["id"]))}
        return {"project_id": run["project_id"], "run_id": run["id"],
                "attempt_id": attempt_id, "task": task,
                "spec_ref": {"id": plan["spec_id"], "version": plan["spec_version"]},
                "spec_context": self._spec_context(run["project_id"],
                                                   plan["spec_version"], task),
                "base_commit": plan["base_commit"],
                "workspace_id": f"{run['id']}/{task['id']}",
                "inputs": inputs, "supplied_inputs": supplied}

    def _spec_context(self, project_id, spec_version, task):
        decisions = [dict(row) for row in self.db.execute(
            "SELECT id, scope, choice, reason FROM spec_decisions"
            " WHERE project_id=? AND spec_version=?", (project_id, spec_version))]
        open_items = [dict(row) for row in self.db.execute(
            "SELECT id, question, blocking, resolution FROM spec_open_items"
            " WHERE project_id=? AND spec_version=?", (project_id, spec_version))]
        wanted_requirements = set(task.get("requirement_ids", []))
        wanted_decisions = set(task.get("decision_ids", []))
        wanted_items = set(task.get("open_item_ids", []))
        requirements = [dict(row) for row in self.db.execute(
            "SELECT id, title, text FROM requirement_items WHERE project_id=?"
            " AND requirement_version=(SELECT COALESCE(MAX(version), 0) FROM requirements"
            " WHERE project_id=?)", (project_id, project_id))
            if row["id"] in wanted_requirements]
        return {"requirements": requirements,
                "decisions": [item for item in decisions if item["id"] in wanted_decisions],
                "open_items": [item for item in open_items if item["id"] in wanted_items]}

    def _next_attempt_number(self, db, run_id, task_id):
        row = db.execute("SELECT COALESCE(MAX(number), 0) AS last FROM attempts"
                         " WHERE run_id=? AND task_id=?", (run_id, task_id)).fetchone()
        return row["last"] + 1

    def _approved_inputs(self, db, run_id, deps):
        inputs = []
        for task_id in deps:
            row = db.execute(
                "SELECT id, version, commit_sha FROM artifacts"
                " WHERE run_id=? AND task_id=? AND status='approved'"
                " ORDER BY version DESC LIMIT 1", (run_id, task_id)).fetchone()
            if row:
                inputs.append({"id": task_id, "version": row["version"],
                               "commit_sha": row["commit_sha"]})
        return inputs

    def renew(self, run_id, attempt_id, worker_id, lease_seconds=300):
        with self.tx() as db:
            attempt = db.execute("SELECT * FROM attempts WHERE run_id=? AND id=?",
                                 (run_id, attempt_id)).fetchone()
            if attempt is None or attempt["state"] != "running" or attempt["worker_id"] != worker_id:
                raise StaleAttempt(current_attempt_id=self._current_attempt(db, run_id,
                                                                            attempt["task_id"]
                                                                            if attempt else None))
            lease = _stamp(self.now() + timedelta(seconds=lease_seconds))
            db.execute("UPDATE attempts SET lease_until=? WHERE id=?", (lease, attempt_id))
            return lease

    # ---------- reports ----------

    def report(self, run_id, attempt_id, worker_id, status, artifacts=None, error=None,
               provider=None, model=None, usage=None, checkout_commit=None):
        if status not in ("succeeded", "failed", "input_required"):
            raise InvalidReference("回报状态无效", field="status", status=status)
        payload_hash = _dump({"status": status, "artifacts": artifacts or [],
                              "error": error or {}})
        expired, stale_id = False, None
        with self.tx() as db:
            run, rows = self._task_map(db, run_id)
            attempt = db.execute("SELECT * FROM attempts WHERE run_id=? AND id=?",
                                 (run_id, attempt_id)).fetchone()
            if attempt is None:
                raise StaleAttempt(current_attempt_id=self._current_attempt(db, run_id, None))
            if attempt["report_hash"] == payload_hash:
                return self.snapshot(run_id)           # idempotent replay
            if attempt["state"] != "running" or attempt["worker_id"] != worker_id:
                raise StaleAttempt(current_attempt_id=self._current_attempt(
                    db, run_id, attempt["task_id"]))
            if attempt["lease_until"] and attempt["lease_until"] < _stamp(self.now()):
                self._timeout(db, run_id, attempt)
                self._bump(db, run_id)
                expired = True
                stale_id = self._current_attempt(db, run_id, attempt["task_id"])
            else:
                task_id = attempt["task_id"]
                task = next(item for item in self.plan_tasks(
                    run["project_id"], run["plan_id"], run["plan_version"])
                    if item["id"] == task_id)
                now = _stamp(self.now())
                if status == "succeeded":
                    problems = self._check_git(task, attempt, artifacts or [])
                    if problems:
                        db.execute("UPDATE attempts SET state='failed', ended_at=?,"
                                   " error=?, report_hash=? WHERE id=?",
                                   (now, _dump({"code": "OUTPUT_INVALID",
                                                "problems": problems}),
                                    payload_hash, attempt_id))
                        db.execute("UPDATE run_tasks SET state=? WHERE run_id=?"
                                   " AND task_id=?", (FAILED, run_id, task_id))
                        self._emit(db, run_id, "task.failed", task_id=task_id,
                                   attempt_id=attempt_id,
                                   payload={"code": "OUTPUT_INVALID",
                                            "problems": problems})
                    else:
                        self._accept(db, run_id, task, attempt, artifacts or [], now,
                                     payload_hash)
                elif status == "failed":
                    db.execute("UPDATE attempts SET state='failed', ended_at=?, error=?,"
                               " report_hash=? WHERE id=?",
                               (now, _dump(error or {}), payload_hash, attempt_id))
                    db.execute("UPDATE run_tasks SET state=? WHERE run_id=? AND task_id=?",
                               (FAILED, run_id, task_id))
                    self._emit(db, run_id, "task.failed", task_id=task_id,
                               attempt_id=attempt_id,
                               payload={"code": (error or {}).get("code",
                                                                  "EXECUTION_FAILED")})
                else:
                    missing = list((error or {}).get("details", {}).get("item_ids", []))
                    db.execute("UPDATE attempts SET state='input_required', ended_at=?,"
                               " error=?, report_hash=? WHERE id=?",
                               (now, _dump(error or {}), payload_hash, attempt_id))
                    db.execute(
                        "UPDATE run_tasks SET state=?, missing_input_ids=?, blockers=?"
                        " WHERE run_id=? AND task_id=?",
                        (INPUT_REQUIRED, _dump(missing),
                         _dump([{"code": "MISSING_INPUT", "ref_id": item,
                                 "message": "等待补充输入"} for item in missing]),
                         run_id, task_id))
                db.execute("UPDATE attempts SET provider=?, model=?, usage=?,"
                           " checkout_commit=? WHERE id=?",
                           (provider, model, _dump(usage) if usage else None,
                            checkout_commit, attempt_id))
                self._recompute(db, run_id)
                self._bump(db, run_id)
        if expired:
            raise StaleAttempt(current_attempt_id=stale_id)
        return self.snapshot(run_id)

    def _check_git(self, task, attempt, artifacts):
        """Reject a commit the plan cannot account for (§5 verification)."""
        if self.verifier is None:
            return []
        kinds = {output["id"]: output["kind"] for output in task["outputs"]}
        commits = [item for item in artifacts
                   if kinds.get(item.get("output_id")) == "git_commit"]
        if not commits:
            return []
        required = [ref.get("commit_sha") for ref in _load(attempt["input_refs"], [])
                    if ref.get("commit_sha")]
        problems = []
        for item in commits:
            try:
                self.verifier.verify(commit_sha=item.get("commit_sha"),
                                     checkout_commit=attempt["checkout_commit"],
                                     required_shas=required,
                                     allowed_paths=task["write_paths"])
            except DomainError as exc:
                problems.append({"message": exc.message, "details": exc.details})
        return problems

    def submit(self, run_id, result, worker_id):
        """Route a WorkerResult dict (contract §5) through report()."""
        if not isinstance(result, dict) or not result.get("attempt_id"):
            raise InvalidReference("回报缺少尝试编号", field="attempt_id")
        return self.report(run_id, result["attempt_id"], worker_id,
                           result.get("status", "failed"),
                           result.get("artifacts") or [],
                           result.get("error"),
                           provider=result.get("provider"),
                           model=result.get("model"),
                           usage=result.get("usage"),
                           checkout_commit=result.get("checkout_commit"))

    def _accept(self, db, run_id, task, attempt, artifacts, now, report_hash):
        task_id = task["id"]
        outputs = {output["id"]: output for output in task["outputs"]}
        unknown = [item.get("output_id") for item in artifacts
                   if item.get("output_id") not in outputs]
        if unknown:
            raise InvalidOutput("回报了未声明的输出", field="artifacts", unknown=unknown)
        missing = [output_id for output_id in outputs
                   if output_id not in {item.get("output_id") for item in artifacts}]
        if missing:
            raise InvalidOutput("成功回报缺少输出", field="artifacts", missing=missing)
        db.execute("UPDATE attempts SET state='succeeded', ended_at=?, report_hash=?,"
                   " error='{}' WHERE id=?",
                   (now, report_hash, attempt["id"]))
        needs_review = False
        for item in artifacts:
            output = outputs[item["output_id"]]
            version = self._next_artifact_version(db, run_id, task_id, output["id"])
            approved = not task["review_required"]
            needs_review = needs_review or task["review_required"]
            artifact_id = f"ART-{task_id}-{output['id']}"
            db.execute(
                "INSERT INTO artifacts(id, run_id, task_id, attempt_id, output_id, version,"
                " based_on_version, kind, origin, status, commit_sha, changed_paths, content,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, task_id, attempt["id"], output["id"], version, None,
                 output["kind"], "agent", "approved" if approved else "draft",
                 item.get("commit_sha"), _dump(item.get("changed_paths", [])),
                 item.get("content"), now))
            self._emit(db, run_id, "artifact.submitted", task_id=task_id,
                       attempt_id=attempt["id"],
                       payload={"artifact_id": artifact_id, "version": version,
                                "output_id": output["id"]})
            if approved:
                self._emit(db, run_id, "artifact.approved", task_id=task_id,
                           attempt_id=attempt["id"],
                           payload={"artifact_id": artifact_id, "version": version})
        db.execute("UPDATE run_tasks SET state=? WHERE run_id=? AND task_id=?",
                   (REVIEW if needs_review else DONE, run_id, task_id))

    def _next_artifact_version(self, db, run_id, task_id, output_id):
        row = db.execute("SELECT COALESCE(MAX(version), 0) AS last FROM artifacts"
                         " WHERE run_id=? AND task_id=? AND output_id=?",
                         (run_id, task_id, output_id)).fetchone()
        return row["last"] + 1

    def _timeout(self, db, run_id, attempt):
        now = _stamp(self.now())
        db.execute("UPDATE attempts SET state='failed', ended_at=?, error=?"
                   " WHERE id=?",
                   (now, _dump({"code": "WORKER_TIMEOUT", "message": "租约到期"}), attempt["id"]))
        db.execute("UPDATE run_tasks SET state=? WHERE run_id=? AND task_id=?",
                   (FAILED, run_id, attempt["task_id"]))
        self._emit(db, run_id, "task.failed", task_id=attempt["task_id"],
                   attempt_id=attempt["id"], payload={"code": "WORKER_TIMEOUT"})

    def expire_leases(self, run_id=None):
        clause, params = ("", ()) if run_id is None else (" AND run_id=?", (run_id,))
        rows = self.db.execute(
            "SELECT * FROM attempts WHERE state='running' AND lease_until < ?" + clause,
            (_stamp(self.now()),) + params).fetchall()
        released = 0
        for attempt in rows:
            with self.tx() as db:
                current = db.execute("SELECT * FROM attempts WHERE id=?",
                                     (attempt["id"],)).fetchone()
                if current is None or current["state"] != "running":
                    continue
                self._timeout(db, current["run_id"], current)
                self._recompute(db, current["run_id"])
                self._bump(db, current["run_id"])
                released += 1
        return released

    # ---------- human operations ----------

    def review(self, run_id, task_id, decision, expected_versions, revisions=None,
               note="", actor_id="tester"):
        revisions = revisions or {}
        if decision not in ("approve", "revise", "reject"):
            raise InvalidReference("审核决定无效", field="decision")
        if decision == "reject" and not str(note).strip():
            raise InvalidReference("退回必须填写备注", field="note")
        with self.tx() as db:
            run, rows = self._task_map(db, run_id)
            row = rows.get(task_id)
            if row is None or row["state"] != REVIEW:
                raise InvalidState("该任务当前无需审核", task_id=task_id)
            drafts = {artifact["output_id"]: dict(artifact) for artifact in db.execute(
                "SELECT * FROM artifacts WHERE run_id=? AND task_id=? AND status='draft'",
                (run_id, task_id))}
            if _dump(sorted(drafts)) != _dump(sorted(expected_versions or {})):
                raise VersionConflict(current_versions={key: value["version"]
                                                        for key, value in drafts.items()})
            for output_id, artifact in drafts.items():
                if str(artifact["version"]) != str((expected_versions or {}).get(output_id)):
                    raise VersionConflict(current_versions={output_id: artifact["version"]})
            if decision == "reject":
                db.execute("UPDATE artifacts SET status='rejected'"
                           " WHERE run_id=? AND task_id=? AND status='draft'", (run_id, task_id))
                db.execute("UPDATE run_tasks SET state=? WHERE run_id=? AND task_id=?",
                           (FAILED, run_id, task_id))
                self._emit(db, run_id, "artifact.rejected", task_id=task_id,
                           payload={"note": note})
            else:
                for output_id, artifact in drafts.items():
                    new_version = artifact["version"]
                    if decision == "revise" and output_id in revisions:
                        payload = revisions[output_id]
                        new_version = artifact["version"] + 1
                        db.execute(
                            "INSERT INTO artifacts(id, run_id, task_id, attempt_id, output_id,"
                            " version, based_on_version, kind, origin, status, commit_sha,"
                            " changed_paths, content, created_at)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (artifact["id"], run_id, task_id, artifact["attempt_id"], output_id,
                             new_version, artifact["version"], artifact["kind"], "human",
                             "approved", payload.get("commit_sha", artifact["commit_sha"]),
                             _dump(payload.get("changed_paths", [])),
                             payload.get("content", artifact["content"]),
                             _stamp(self.now())))
                        # the original stays readable and approved; the review
                        # record carries the version that downstream now uses
                        db.execute("UPDATE artifacts SET status='approved' WHERE id=? AND"
                                   " run_id=? AND version=?", (artifact["id"], run_id,
                                                               artifact["version"]))
                    else:
                        db.execute("UPDATE artifacts SET status='approved' WHERE id=? AND"
                                   " run_id=? AND version=?",
                                   (artifact["id"], run_id, artifact["version"]))
                    self._emit(db, run_id, "artifact.approved", task_id=task_id,
                               payload={"artifact_id": artifact["id"], "version": new_version})
                db.execute("UPDATE run_tasks SET state=? WHERE run_id=? AND task_id=?",
                           (DONE, run_id, task_id))
            db.execute(
                "INSERT INTO review_records(run_id, task_id, decision, expected_versions,"
                " approved_versions, note, actor_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, task_id, decision, _dump(expected_versions or {}),
                 _dump(revisions), note, actor_id, _stamp(self.now())))
            self._recompute(db, run_id)
            self._bump(db, run_id)
            return self.snapshot(run_id)

    def retry(self, run_id, task_id):
        with self.tx() as db:
            run, rows = self._task_map(db, run_id)
            row = rows.get(task_id)
            if row is None or row["state"] != FAILED:
                raise InvalidState("仅可重试失败任务", task_id=task_id)
            db.execute("UPDATE run_tasks SET state=?, blockers='[]' WHERE run_id=? AND task_id=?",
                       (WAITING, run_id, task_id))
            self._emit(db, run_id, "task.retry_requested", task_id=task_id)
            self._recompute(db, run_id)
            self._bump(db, run_id)
            return self.snapshot(run_id)

    def supply_input(self, run_id, task_id, values, submitted_by=None):
        with self.tx() as db:
            run, rows = self._task_map(db, run_id)
            row = rows.get(task_id)
            if row is None or row["state"] != INPUT_REQUIRED:
                raise InvalidState("该任务当前不需要补充输入", task_id=task_id)
            missing = _load(row["missing_input_ids"], [])
            unknown = [key for key in values if key not in missing]
            if unknown:
                raise InvalidReference("提交了非当前缺项的编号", field="values", unknown=unknown)
            if any(not str(value).strip() for value in values.values()):
                raise InvalidReference("补充内容不能为空", field="values")
            left = [item for item in missing if item not in values]
            db.execute("UPDATE run_tasks SET missing_input_ids=?, state=? WHERE run_id=?"
                       " AND task_id=?", (_dump(left), WAITING, run_id, task_id))
            for item, text in values.items():
                db.execute(
                    "INSERT INTO input_submissions(run_id, task_id, item_id, value,"
                    " submitted_by, created_at) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(run_id, task_id, item_id) DO UPDATE SET"
                    " value=excluded.value, submitted_by=excluded.submitted_by",
                    (run_id, task_id, item, text, submitted_by, _stamp(self.now())))
                self._emit(db, run_id, "task.input_supplied", task_id=task_id,
                           payload={"item_id": item})
            self._recompute(db, run_id)
            self._bump(db, run_id)
            return self.snapshot(run_id)

    def set_dispatch(self, run_id, paused):
        with self.tx() as db:
            run, _ = self._task_map(db, run_id)
            db.execute("UPDATE runs SET dispatch=? WHERE id=?",
                       ("paused" if paused else "active", run_id))
            if paused:      # the contract clears unconsumed manual slots
                db.execute("DELETE FROM dispatch_queue WHERE run_id=?", (run_id,))
            self._emit(db, run_id, "run.paused" if paused else "run.resumed")
            self._bump(db, run_id)
            return self.snapshot(run_id)

    # ---------- reads ----------

    def snapshot(self, run_id):
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise NotFound("运行不存在", run_id=run_id)
        tasks = {}
        for row in self.db.execute("SELECT * FROM run_tasks WHERE run_id=?", (run_id,)):
            tasks[row["task_id"]] = {
                "task_id": row["task_id"], "state": row["state"],
                "blockers": _load(row["blockers"], []),
                "missing_input_ids": _load(row["missing_input_ids"], []),
                "attempts": [dict(attempt) for attempt in self.db.execute(
                    "SELECT id, number, state, worker_id, lease_until, created_at,"
                    " started_at, ended_at, error, provider, model, usage,"
                    " checkout_commit FROM attempts WHERE run_id=? AND task_id=?"
                    " ORDER BY number", (run_id, row["task_id"]))],
                "artifacts": [dict(artifact) for artifact in self.db.execute(
                    "SELECT id, output_id, version, kind, origin, status, commit_sha,"
                    " based_on_version FROM artifacts WHERE run_id=? AND task_id=?"
                    " ORDER BY output_id, version", (run_id, row["task_id"]))],
            }
        return {"id": run["id"], "project_id": run["project_id"],
                "plan_ref": {"id": run["plan_id"], "version": run["plan_version"]},
                "mode": run["mode"], "dispatch": run["dispatch"], "state": run["state"],
                "revision": run["revision"], "started_at": run["started_at"],
                "ended_at": run["ended_at"], "latest_event_seq": run["latest_event_seq"],
                "tasks": tasks,
                "findings": [{"code": row["code"], "ref_id": row["ref_id"],
                              "stage": row["stage"], "severity": row["severity"],
                              "summary": row["summary"],
                              "evidence_refs": _load(row["evidence_refs"], []),
                              "affected_task_ids": _load(row["affected_task_ids"], []),
                              "suggested_action": row["suggested_action"]}
                             for row in self.db.execute(
                                 "SELECT * FROM findings WHERE run_id=?"
                                 " ORDER BY severity, code, ref_id", (run_id,))]}

    def events(self, run_id, after=0, limit=100):
        rows = self.db.execute(
            "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
            (run_id, after, limit + 1)).fetchall()
        items = [{"seq": row["seq"], "type": row["type"], "run_id": run_id,
                  "task_id": row["task_id"], "attempt_id": row["attempt_id"],
                  "created_at": row["created_at"], "payload": _load(row["payload"], {})}
                 for row in rows[:limit]]
        return {"items": items, "next_after": items[-1]["seq"] if items else after,
                "has_more": len(rows) > limit}

    # ---------- internals ----------

    def _current_attempt(self, db, run_id, task_id):
        if task_id is None:
            return None
        row = db.execute("SELECT id FROM attempts WHERE run_id=? AND task_id=?"
                         " ORDER BY number DESC LIMIT 1", (run_id, task_id)).fetchone()
        return row["id"] if row else None

    def _emit(self, db, run_id, kind, task_id=None, attempt_id=None, payload=None):
        row = db.execute("SELECT latest_event_seq FROM runs WHERE id=?", (run_id,)).fetchone()
        seq = row["latest_event_seq"] + 1
        db.execute("INSERT INTO events(run_id, seq, type, task_id, attempt_id, created_at,"
                   " payload) VALUES (?,?,?,?,?,?,?)",
                   (run_id, seq, kind, task_id, attempt_id, _stamp(self.now()),
                    _dump(payload or {})))
        db.execute("UPDATE runs SET latest_event_seq=? WHERE id=?", (seq, run_id))

    def _bump(self, db, run_id):
        db.execute("UPDATE runs SET revision=revision+1 WHERE id=?", (run_id,))