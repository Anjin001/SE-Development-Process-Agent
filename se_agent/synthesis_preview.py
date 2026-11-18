"""In-memory HSX preview. Run: python -m se_agent.synthesis_preview --port 8002."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from . import synthesis
from .agent_client import AgentError, configured_agent

ROOT = Path(__file__).resolve().parent.parent


class PreviewError(ValueError):
    def __init__(self, code, message, status=409, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def _records(project, spec, action, actor="human"):
    records = []
    if action == "propose":
        for draft in project["drafts"]:
            for claim in draft["claims"]:
                related = [d for d in spec["decisions"] if draft["id"] in d["draft_ids"]
                           and set(claim["requirement_ids"]).intersection(d["requirement_ids"])]
                deferred = any(claim["id"] in item["question"] or any(o["claim_id"] == claim["id"] for o in item.get("options", []))
                               for item in spec["open_items"])
                if spec["selection"] not in ("merge", draft["id"]):
                    disposition, reason = "舍弃", "未作为本轮方案起点；不表示该建议已被永久否决。"
                elif deferred or not related:
                    disposition, reason = "延期", "尚待人工决定或补齐，查看相关未决事项。"
                else:
                    disposition = "组合" if any(len(d["draft_ids"]) > 1 for d in related) else "保留"
                    reason = "依据需求和草案编号关联到候选决策，具体语义须人工复查。"
                records.append({"version": spec["version"], "action": disposition,
                                "source_refs": [draft["id"], claim["id"], *claim["requirement_ids"]], "reason": reason})
    else:
        by = "主理人 AI" if actor == "steward" else "人工"
        previous = project["versions"][-1]
        records.append({"version": spec["version"], "action": "修改", "source_refs": [f"{previous['id']}@{previous['version']}"],
                        "reason": by + "保存了正文、决策和未决事项；可对照版本查看变更。"})
        current_ids = {d["id"] for d in spec["decisions"]}
        for decision in previous["decisions"]:
            if decision["id"] not in current_ids:
                records.append({"version": spec["version"], "action": "舍弃", "source_refs": [decision["id"]],
                                "reason": by + "在本版移除了该决策；原版本仍可追溯。"})
    return records


class PreviewStore:
    """Demo-only service layer; IDs and confirmations never come from agents."""

    def __init__(self, agent=None):
        self.projects = {}
        self.agent = {"agent": agent} if agent else {}

    def create(self, payload):
        synthesis.validate_handoff(payload.get("requirement"), payload.get("drafts"))
        project = {"id": uuid4().hex, "requirement": deepcopy(payload["requirement"]),
                   "drafts": sorted(deepcopy(payload["drafts"]), key=lambda d: d["id"]),
                   "evaluations": [], "versions": [], "review_records": [],
                   "round": 1, "round_changes": [], "version_sources": {}}
        self.projects[project["id"]] = project
        return self.snapshot(project)

    def get(self, project_id):
        if project_id not in self.projects:
            raise PreviewError("NOT_FOUND", "演示项目不存在或服务已重启，请重新导入。", 404)
        return self.projects[project_id]

    def snapshot(self, project):
        previous = project["versions"][-1] if project["versions"] else None
        latest = previous if previous and previous["round"] == project["round"] else None
        return deepcopy({"id": project["id"], "mode": "model" if self.agent else "mock", "requirement": project["requirement"],
                         "round": project["round"], "round_changes": project["round_changes"],
                         "latest_version": previous["version"] if previous else 0,
                         "drafts": project["drafts"], "evaluations": project["evaluations"], "specification": latest,
                         "versions": [{"id": v["id"], "version": v["version"], "status": v["status"],
                                       "selection": v["selection"], "round": v["round"]} for v in project["versions"]],
                         "review_records": project["review_records"],
                         "findings": self.findings(project, latest) if latest else []})

    @staticmethod
    def findings(project, spec):
        required = [item["id"] for item in project["requirement"]["items"]]
        findings = [{"code": "REFERENCES_OK", "version": spec["version"], "severity": "info",
                     "summary": f"{len(required)} 条需求都能追溯到决策或待处理事项。",
                     "evidence_refs": required, "suggestion": "请人工核对规则含义和取舍。"}]
        findings.extend({"code": "OPEN_ITEM", "version": spec["version"], "ref_id": item["id"],
                         "summary": item["question"], "blocking": item["blocking"], "severity": "warning",
                         "requirement_ids": item["affected_requirement_ids"],
                         "evidence_refs": [item["id"], *item["affected_requirement_ids"],
                                           *(o["claim_id"] for o in item.get("options", []))],
                         "suggestion": "请填写人工答复；也可保留未决并交给后续环节。"}
                        for item in spec["open_items"] if item["resolution"] is None)
        return findings

    def version(self, project_id, spec_id, version):
        project = self.get(project_id)
        for spec in project["versions"]:
            if spec["id"] == spec_id and spec["version"] == version:
                return deepcopy(spec)
        raise PreviewError("NOT_FOUND", "规格版本不存在", 404)

    def version_handoff(self, project_id, spec_id, version):
        spec = self.version(project_id, spec_id, version)
        project = self.get(project_id)
        return deepcopy({"requirement": project["requirement"], **project["version_sources"][version],
                         "specification": spec, "review_records":
                             [record for record in project["review_records"] if record["version"] <= version]})

    def update_drafts(self, project, payload):
        if payload.get("expected_round") != project["round"]:
            raise PreviewError("VERSION_CONFLICT", "赛马轮次已更新，请刷新。", current_round=project["round"])
        if payload.get("requirement") != project["requirement"]:
            raise PreviewError("INVALID_STATE", "当前项目绑定另一份需求快照，请另建评审项目。")
        drafts = payload.get("drafts")
        synthesis.validate_handoff(project["requirement"], drafts)
        drafts = sorted(deepcopy(drafts), key=lambda d: d["id"])
        if drafts == project["drafts"]:
            return self.snapshot(project)

        def claims(rows):
            return {c["id"]: {"group_id": d["id"], "text": c["text"], "requirement_ids": c["requirement_ids"]}
                    for d in rows for c in d["claims"]}
        before, after = claims(project["drafts"]), claims(drafts)
        differences = [{"claim_id": id_, "before": before.get(id_), "after": after.get(id_)}
                       for id_ in sorted(before.keys() | after.keys()) if before.get(id_) != after.get(id_)]
        affected = {ref for change in differences for side in ("before", "after")
                    for ref in (change[side] or {}).get("requirement_ids", [])}
        previous = project["versions"][-1] if project["versions"] else None
        old_groups = {d["id"]: d for d in project["drafts"]}
        new_groups = {d["id"]: d for d in drafts}
        changed_groups = sorted(id_ for id_ in old_groups.keys() | new_groups.keys()
                                if old_groups.get(id_) != new_groups.get(id_))
        project["round_changes"].append({
            "round": project["round"] + 1, "changed_groups": changed_groups, "claims": differences,
            "prior_decisions": [{"id": d["id"], "choice": d["choice"], "requirement_ids": d["requirement_ids"],
                                 "needs_review": bool(affected.intersection(d["requirement_ids"]))}
                                for d in (previous["decisions"] if previous else [])]
        })
        project.update(drafts=drafts, evaluations=[], round=project["round"] + 1)
        return self.snapshot(project)

    @staticmethod
    def edited_content(payload):
        content = {key: deepcopy(payload.get(key)) for key in ("markdown", "decisions", "open_items")}
        notes = payload.get("notes_markdown", "")
        if not isinstance(notes, str):
            raise PreviewError("INVALID_REQUEST", "人工补充说明必须为文本。", 400)
        content["notes_markdown"] = notes
        return content

    @staticmethod
    def protect_issues(latest, content):
        if not latest:
            return
        pending = {item["id"]: item for item in content["open_items"]}
        for issue in latest["open_items"]:
            if not issue.get("options"):
                continue
            current = pending.get(issue["id"])
            if (not current or current.get("options") != issue["options"]
                    or current["affected_requirement_ids"] != issue["affected_requirement_ids"]
                    or (current["resolution"] is None and not current["blocking"])):
                raise synthesis.SynthesisError("请在原议题上处理候选选项，保留来源和影响范围。",
                                               "proposal.open_items", "INVALID_OUTPUT")

    def change(self, project_id, action, payload, progress=None, actor="human"):
        project = self.get(project_id)
        previous = project["versions"][-1] if project["versions"] else None
        latest = previous if previous and previous["round"] == project["round"] else None
        if action == "update-drafts":
            return self.update_drafts(project, payload)
        if action == "evaluate":
            if latest:
                raise PreviewError("INVALID_STATE", "已有方案版本；更换输入请新建演示项目。")
            evaluations = []
            for draft in project["drafts"]:
                if progress:
                    progress(f"独立评价 {draft['id']} 组草案", 60 if self.agent else 0)
                evaluations.append(synthesis.evaluate_draft(project["requirement"], draft, **self.agent))
            project["evaluations"] = evaluations
            return self.snapshot(project)
        if action not in ("propose", "save", "confirm", "inspect"):
            raise PreviewError("NOT_FOUND", "操作不存在", 404)
        current = previous["version"] if previous else 0
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise PreviewError("INVALID_REQUEST", "expected_version 必须是非负整数", 400)
        if expected != current:
            raise PreviewError("VERSION_CONFLICT", "方案已变化，请刷新后核对；本次修改未保存。", current_version=current)
        if action == "inspect":
            if not latest:
                raise PreviewError("INVALID_STATE", "请先生成方案草稿。")
            content = self.edited_content(payload)
            try:
                synthesis.validate_proposal(project["requirement"], project["drafts"], content)
                self.protect_issues(latest, content)
            except synthesis.SynthesisError as error:
                return {"version": current, "findings": [
                    {"code": error.code, "severity": "error", "summary": str(error),
                     "evidence_refs": [error.path] if error.path else [],
                     "suggestion": "检查对应字段和需求来源，修正后再保存。"}
                ]}
            markdown = synthesis.render_spec_markdown(project["requirement"], content["decisions"], content["open_items"], content["notes_markdown"])
            return {"version": current, "markdown": markdown, "findings": self.findings(project, {**content, "version": current})}
        if action == "confirm":
            if not latest or latest["status"] != "draft":
                raise PreviewError("INVALID_STATE", "只有最新草稿可以确认。")
            synthesis.validate_proposal(project["requirement"], project["drafts"], latest)
            if latest["requirement_ref"] != {"id": project["requirement"]["id"], "version": project["requirement"]["version"]}:
                raise PreviewError("VERSION_CONFLICT", "绑定的需求版本已变化。", current_version=current)
            latest.update(status="confirmed", confirmed_by="steward" if actor == "steward" else "lead-demo", confirmed_at=datetime.now(timezone.utc).isoformat())
            return self.snapshot(project)
        if action == "propose":
            if progress:
                progress("比较各组方案并生成综合草稿", 60 if self.agent else 0)
            content = synthesis.propose_spec(project["requirement"], project["drafts"], project["evaluations"], payload.get("selection"), **self.agent)
            content["notes_markdown"] = ""
            selection = payload["selection"]
        else:
            if not latest:
                raise PreviewError("INVALID_STATE", "请先生成方案草稿。")
            content = self.edited_content(payload)
            synthesis.validate_proposal(project["requirement"], project["drafts"], content)
            self.protect_issues(latest, content)
            content["markdown"] = synthesis.render_spec_markdown(project["requirement"], content["decisions"], content["open_items"], content["notes_markdown"])
            selection = latest["selection"]
        spec = {"id": previous["id"] if previous else "SPEC-" + project_id[:8], "version": current + 1, "status": "draft",
                "round": project["round"],
                "requirement_ref": {"id": project["requirement"]["id"], "version": project["requirement"]["version"]},
                "selection": selection, **content, "confirmed_by": None, "confirmed_at": None}
        records = _records(project, spec, action, actor)
        project["version_sources"][spec["version"]] = deepcopy({"drafts": project["drafts"], "evaluations": project["evaluations"]})
        project["versions"].append(spec)
        project["review_records"].extend(records)
        return self.snapshot(project)


class PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    def respond(self, status, data, content_type="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        store = self.server.store
        try:
            if self.command == "GET" and parts == [""]:
                self.respond(200, (ROOT / "prototypes" / "review-synthesis.html").read_bytes(), "text/html; charset=utf-8")
                return
            if self.command == "GET" and len(parts) == 3 and parts[:2] == ["preview", "examples"]:
                samples = {"conflict": "conflict.json", "cly": "cly-handoff.json"}
                if parts[2] not in samples:
                    raise PreviewError("NOT_FOUND", "样例不存在", 404)
                self.respond(200, json.loads((ROOT / "examples" / "review-synthesis" / samples[parts[2]]).read_text(encoding="utf-8")))
                return
            payload = {}
            if self.command == "POST":
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000:
                    raise PreviewError("INVALID_REQUEST", "请求体须为 1MB 以内的 JSON", 400)
                def invalid_constant(value):
                    raise ValueError("不支持 JSON 常量 " + value)
                payload = json.loads(self.rfile.read(size).decode("utf-8-sig"), parse_constant=invalid_constant)
                if not isinstance(payload, dict):
                    raise PreviewError("INVALID_REQUEST", "请求体必须是 JSON 对象", 400)
            if parts == ["preview", "projects"] and self.command == "POST":
                self.respond(201, store.create(payload))
                return
            if len(parts) >= 3 and parts[:2] == ["preview", "projects"]:
                if len(parts) == 3 and self.command == "GET":
                    self.respond(200, store.snapshot(store.get(parts[2])))
                    return
                if len(parts) == 5 and parts[3] == "actions" and self.command == "POST":
                    tracker = getattr(self.server, "progress", None)
                    if tracker:
                        result = store.change(parts[2], parts[4], payload, progress=lambda label, seconds=0:
                                              tracker.record(payload.get("progress_id"), label, seconds))
                    else:
                        result = store.change(parts[2], parts[4], payload)
                    self.respond(200, result)
                    return
                if len(parts) == 7 and parts[3] == "specifications" and parts[5] == "versions" and self.command == "GET":
                    self.respond(200, store.version(parts[2], parts[4], int(parts[6])))
                    return
                if len(parts) == 8 and parts[3] == "specifications" and parts[5] == "versions" and parts[7] == "handoff" and self.command == "GET":
                    self.respond(200, store.version_handoff(parts[2], parts[4], int(parts[6])))
                    return
            raise PreviewError("NOT_FOUND", "接口不存在", 404)
        except AgentError as error:
            self.respond(502, {"error": {"code": "MODEL_ERROR", "message": str(error), "details": {}}})
        except synthesis.SynthesisError as error:
            self.respond(422, {"error": {"code": error.code, "message": str(error), "details": {"path": error.path}}})
        except PreviewError as error:
            self.respond(error.status, {"error": {"code": error.code, "message": str(error), "details": error.details}})
        except (ValueError, TypeError, UnicodeError) as error:
            self.respond(400, {"error": {"code": "INVALID_REQUEST", "message": str(error), "details": {}}})


def make_preview_server(port=8002):
    server = HTTPServer(("127.0.0.1", port), PreviewHandler)
    server.store = PreviewStore(configured_agent())
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    with make_preview_server(args.port) as server:
        print(f"HSX 评审整合预览 http://127.0.0.1:{server.server_port} · 本地模拟，重启清空", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
