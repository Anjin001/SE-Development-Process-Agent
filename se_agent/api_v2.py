"""HTTP /api/v2 routes for plans, runs and events (docs/接口契约-v2.md §6).

``Router.handle`` is framework-free: it maps (method, path, query, body,
headers) to (status, payload). ``api_v2_http`` mounts it on the stdlib server
today; a FastAPI app can mount the same table later without moving rules.

Scope: this member owns the plan and run API. The requirement, race and
specification routes belong to their modules and are wired into the shared
router by the lead.
"""
import hashlib
import json
import re

from .contracts import (DomainError, IdempotencyConflict, InvalidReference,
                        NotFound, VersionConflict)

PLAN_NEW = re.compile(r"^/api/v2/projects/([^/]+)/plans$")
PLAN_ONE = re.compile(r"^/api/v2/projects/([^/]+)/plans/([^/]+)/versions/(\d+)$")
PLAN_PATCH = re.compile(r"^/api/v2/projects/([^/]+)/plans/([^/]+)$")
RUN_NEW = re.compile(r"^/api/v2/projects/([^/]+)/runs$")
RUN_ONE = re.compile(r"^/api/v2/projects/([^/]+)/runs/([^/]+)$")
RUN_EVENTS = re.compile(r"^/api/v2/projects/([^/]+)/runs/([^/]+)/events$")
RUN_ACTION = re.compile(r"^/api/v2/projects/([^/]+)/runs/([^/]+)/actions/([a-z-]+)$")


def _digest(method, path, body):
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{method} {path} {payload}".encode()).hexdigest()


class Router:
    def __init__(self, store, actor_id="tester"):
        self.store = store
        self.actor_id = actor_id

    def handle(self, method, path, body=None, headers=None, query=None):
        body = body if isinstance(body, dict) else {}
        headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
        query = query or {}
        try:
            return self._dispatch(method.upper(), path, body, headers, query)
        except DomainError as exc:
            return exc.status, exc.as_body()
        except (ValueError, TypeError) as exc:
            return 400, {"error": {"code": "INVALID_ARGUMENT", "message": str(exc),
                                   "details": {}}}

    # ---------- routing ----------

    def _dispatch(self, method, path, body, headers, query):
        match = PLAN_ONE.match(path)
        if match:
            self._expect(method, "GET")
            return 200, self.store.get_plan(match.group(1), match.group(2),
                                            int(match.group(3)))
        match = PLAN_PATCH.match(path)
        if match:
            self._expect(method, "PATCH")
            return self._patch_plan(match.group(1), match.group(2), path, body, headers)
        match = PLAN_NEW.match(path)
        if match:
            self._expect(method, "POST")
            return self._create_plan(match.group(1), path, body, headers)
        match = RUN_ACTION.match(path)
        if match:
            self._expect(method, "POST")
            return self._act(match.group(1), match.group(2), match.group(3), path,
                             body, headers)
        match = RUN_EVENTS.match(path)
        if match:
            self._expect(method, "GET")
            return 200, self._events(match.group(1), match.group(2), query)
        match = RUN_NEW.match(path)
        if match:
            self._expect(method, "POST")
            return self._create_run(match.group(1), path, body, headers)
        match = RUN_ONE.match(path)
        if match:
            self._expect(method, "GET")
            return 200, self._run(match.group(1), match.group(2))
        raise NotFound("接口不存在", path=path)

    def _expect(self, method, wanted):
        if method != wanted:
            raise InvalidReference("方法不被支持", field="method",
                                   expected=wanted, actual=method)

    # ---------- plans ----------

    def _create_plan(self, project_id, path, body, headers):
        spec_ref = body.get("spec_ref") or {}
        tasks = body.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise InvalidReference("需要任务分解模块提供任务草稿", field="tasks")

        def action():
            spec = self.store.get_spec(project_id, spec_ref.get("version"))
            if spec["id"] != spec_ref.get("id"):
                raise VersionConflict(current_version=spec["version"])
            plan = self.store.create_plan(project_id, spec, body.get("base_commit"),
                                          body.get("max_parallel", 2), tasks)
            return 201, self.store.get_plan(project_id, plan["id"], plan["version"])

        return self._idempotent(project_id, "POST", path, body, headers, action)

    def _patch_plan(self, project_id, plan_id, path, body, headers):
        def action():
            return 200, self.store.patch_plan(project_id, plan_id,
                                              body.get("expected_version"),
                                              body.get("max_parallel"),
                                              body.get("tasks"))

        return self._idempotent(project_id, "PATCH", path, body, headers, action)

    # ---------- runs ----------

    def _create_run(self, project_id, path, body, headers):
        plan_ref = body.get("plan_ref") or {}
        if not plan_ref.get("id"):
            raise InvalidReference("缺少计划引用", field="plan_ref")

        def action():
            latest = self.store.latest_plan_version(project_id, plan_ref["id"])
            if plan_ref.get("version") != latest:
                raise VersionConflict(current_version=latest)
            return 201, self.store.create_run(project_id, plan_ref["id"],
                                              plan_ref["version"],
                                              body.get("mode", "manual"))

        return self._idempotent(project_id, "POST", path, body, headers, action)

    def _run(self, project_id, run_id):
        snapshot = self.store.snapshot(run_id)
        if snapshot["project_id"] != project_id:
            raise NotFound("运行不属于该项目", run_id=run_id)
        return snapshot

    def _events(self, project_id, run_id, query):
        self._run(project_id, run_id)
        after = int(query.get("after") or 0)
        limit = min(int(query.get("limit") or 100), 500)
        return self.store.events(run_id, after, limit)

    def _act(self, project_id, run_id, action, path, body, headers):
        self._run(project_id, run_id)

        def dispatch():
            snapshot = self.store.snapshot(run_id)
            expected = body.get("expected_revision")
            if expected is not None and expected != snapshot["revision"]:
                raise VersionConflict(current_revision=snapshot["revision"])
            if action == "pause":
                return 200, self.store.set_dispatch(run_id, True)
            if action == "resume":
                return 200, self.store.set_dispatch(run_id, False)
            if action == "step":
                handed = self.store.claim(run_id, "manual")
                return 200, {"dispatched": [item["attempt_id"] for item in handed],
                             "run": self.store.snapshot(run_id)}
            if action == "retry":
                return 200, self.store.retry(run_id, body.get("task_id"))
            if action == "supply-input":
                return 200, self.store.supply_input(run_id, body.get("task_id"),
                                                    body.get("values") or {})
            if action == "review":
                return 200, self.store.review(run_id, body.get("task_id"),
                                              body.get("decision"),
                                              body.get("expected_versions") or {},
                                              body.get("revisions") or {},
                                              body.get("note", ""), self.actor_id)
            raise InvalidReference("未知动作", field="action", action=action)

        return self._idempotent(project_id, "POST", path, body, headers, dispatch)

    # ---------- idempotency ----------

    def _idempotent(self, scope, method, path, body, headers, action):
        key = headers.get("idempotency-key")
        if not key:
            raise InvalidReference("写请求必须带 Idempotency-Key",
                                   field="Idempotency-Key")
        digest = _digest(method, path, body)
        row = self.store.db.execute(
            "SELECT request_hash, status, body FROM idempotency"
            " WHERE scope=? AND method=? AND path=? AND actor_id=? AND key=?",
            (scope, method, path, self.actor_id, key)).fetchone()
        if row is not None:
            if row["request_hash"] != digest:
                raise IdempotencyConflict(key=key)
            return row["status"], json.loads(row["body"])
        status, payload = action()
        self.store.db.execute(
            "INSERT INTO idempotency(scope, method, path, actor_id, key, request_hash,"
            " status, body, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (scope, method, path, self.actor_id, key, digest, status,
             json.dumps(payload, ensure_ascii=False), self.store.now().isoformat()))
        return status, payload