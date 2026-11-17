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

ROOT = Path(__file__).resolve().parent.parent


class PreviewError(ValueError):
    def __init__(self, code, message, status=409, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def _records(project, spec, action):
    records = []
    if action == "propose":
        for draft in project["drafts"]:
            for claim in draft["claims"]:
                related = [d for d in spec["decisions"] if draft["id"] in d["draft_ids"]
                           and set(claim["requirement_ids"]).intersection(d["requirement_ids"])]
                deferred = any(claim["id"] in item["question"] for item in spec["open_items"])
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
        previous = project["versions"][-1]
        records.append({"version": spec["version"], "action": "修改", "source_refs": [f"{previous['id']}@{previous['version']}"],
                        "reason": "人工保存了正文、决策和未决事项；请查看两个版本核对具体修改。"})
        current_ids = {d["id"] for d in spec["decisions"]}
        for decision in previous["decisions"]:
            if decision["id"] not in current_ids:
                records.append({"version": spec["version"], "action": "舍弃", "source_refs": [decision["id"]],
                                "reason": "人工在本版移除了该决策；原版本仍可追溯。"})
    return records


class PreviewStore:
    """Demo-only service layer; IDs and confirmations never come from agents."""

    def __init__(self):
        self.projects = {}

    def create(self, payload):
        synthesis.validate_handoff(payload.get("requirement"), payload.get("drafts"))
        project = {"id": uuid4().hex, "requirement": deepcopy(payload["requirement"]),
                   "drafts": sorted(deepcopy(payload["drafts"]), key=lambda d: d["id"]),
                   "evaluations": [], "versions": [], "review_records": []}
        self.projects[project["id"]] = project
        return self.snapshot(project)

    def get(self, project_id):
        if project_id not in self.projects:
            raise PreviewError("NOT_FOUND", "演示项目不存在或服务已重启，请重新导入。", 404)
        return self.projects[project_id]

    def snapshot(self, project):
        latest = project["versions"][-1] if project["versions"] else None
        return deepcopy({"id": project["id"], "mode": "mock", "requirement": project["requirement"],
                         "drafts": project["drafts"], "evaluations": project["evaluations"], "specification": latest,
                         "versions": [{"id": v["id"], "version": v["version"], "status": v["status"],
                                       "selection": v["selection"]} for v in project["versions"]],
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
                         "evidence_refs": [item["id"], *item["affected_requirement_ids"]],
                         "suggestion": "请填写人工答复；也可保留未决并交给后续环节。"}
                        for item in spec["open_items"] if item["resolution"] is None)
        return findings

    def version(self, project_id, spec_id, version):
        project = self.get(project_id)
        for spec in project["versions"]:
            if spec["id"] == spec_id and spec["version"] == version:
                return deepcopy(spec)
        raise PreviewError("NOT_FOUND", "规格版本不存在", 404)

    def change(self, project_id, action, payload):
        project = self.get(project_id)
        latest = project["versions"][-1] if project["versions"] else None
        if action == "evaluate":
            if latest:
                raise PreviewError("INVALID_STATE", "已有方案版本；更换输入请新建演示项目。")
            evaluations = [synthesis.evaluate_draft(project["requirement"], d) for d in project["drafts"]]
            project["evaluations"] = evaluations
            return self.snapshot(project)
        if action not in ("propose", "save", "confirm", "inspect"):
            raise PreviewError("NOT_FOUND", "操作不存在", 404)
        current = latest["version"] if latest else 0
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise PreviewError("INVALID_REQUEST", "expected_version 必须是非负整数", 400)
        if expected != current:
            raise PreviewError("VERSION_CONFLICT", "方案已变化，请刷新后核对；本次修改未保存。", current_version=current)
        if action == "inspect":
            if not latest:
                raise PreviewError("INVALID_STATE", "请先生成方案草稿。")
            content = {key: payload.get(key) for key in ("markdown", "decisions", "open_items")}
            try:
                synthesis.validate_proposal(project["requirement"], project["drafts"], content)
            except synthesis.SynthesisError as error:
                return {"version": current, "findings": [
                    {"code": error.code, "severity": "error", "summary": str(error),
                     "evidence_refs": [error.path] if error.path else [],
                     "suggestion": "检查对应字段和需求来源，修正后再保存。"}
                ]}
            return {"version": current, "findings": self.findings(project, {**content, "version": current})}
        if action == "confirm":
            if not latest or latest["status"] != "draft":
                raise PreviewError("INVALID_STATE", "只有最新草稿可以确认。")
            synthesis.validate_proposal(project["requirement"], project["drafts"], latest)
            if latest["requirement_ref"] != {"id": project["requirement"]["id"], "version": project["requirement"]["version"]}:
                raise PreviewError("VERSION_CONFLICT", "绑定的需求版本已变化。", current_version=current)
            latest.update(status="confirmed", confirmed_by="lead-demo", confirmed_at=datetime.now(timezone.utc).isoformat())
            return self.snapshot(project)
        if action == "propose":
            content = synthesis.propose_spec(project["requirement"], project["drafts"], project["evaluations"], payload.get("selection"))
            selection = payload["selection"]
        else:
            if not latest:
                raise PreviewError("INVALID_STATE", "请先生成方案草稿。")
            content = {key: deepcopy(payload.get(key)) for key in ("markdown", "decisions", "open_items")}
            synthesis.validate_proposal(project["requirement"], project["drafts"], content)
            selection = latest["selection"]
        spec = {"id": latest["id"] if latest else "SPEC-" + project_id[:8], "version": current + 1, "status": "draft",
                "requirement_ref": {"id": project["requirement"]["id"], "version": project["requirement"]["version"]},
                "selection": selection, **content, "confirmed_by": None, "confirmed_at": None}
        records = _records(project, spec, action)
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
                    self.respond(200, store.change(parts[2], parts[4], payload))
                    return
                if len(parts) == 7 and parts[3] == "specifications" and parts[5] == "versions" and self.command == "GET":
                    self.respond(200, store.version(parts[2], parts[4], int(parts[6])))
                    return
            raise PreviewError("NOT_FOUND", "接口不存在", 404)
        except synthesis.SynthesisError as error:
            self.respond(422, {"error": {"code": error.code, "message": str(error), "details": {"path": error.path}}})
        except PreviewError as error:
            self.respond(error.status, {"error": {"code": error.code, "message": str(error), "details": error.details}})
        except (ValueError, TypeError, UnicodeError) as error:
            self.respond(400, {"error": {"code": "INVALID_REQUEST", "message": str(error), "details": {}}})


def make_preview_server(port=8002):
    server = HTTPServer(("127.0.0.1", port), PreviewHandler)
    server.store = PreviewStore()
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
