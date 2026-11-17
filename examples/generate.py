"""Refresh examples from the current mock contract. Run: python -m examples.generate."""
import json
from pathlib import Path
from unittest.mock import patch

from se_agent import runner, server

examples = Path(__file__).resolve().parent
project = server.create((examples / "requirement.md").read_text(encoding="utf-8"))
project["id"] = "example-project"


def write(name, record):
    (examples / name).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def capture(request):
    if request["task"]["id"] == "T4":
        write("worker-request.json", request)
    return execute(request)


write("01-race.json", server.snapshot(project))
server.change(project, "confirm", {"decision": "merge"})
write("02-plan.json", server.snapshot(project))
server.change(project, "start", {"version": 1})
server.change(project, "step", {})
server.change(project, "step", {})
write("03-review.json", server.snapshot(project))
server.change(project, "review", {"task_id": "T2", "version": 1, "decision": "revise",
                                  "content": "人工修订：停用设备保留在列表中，并隐藏新预约入口。",
                                  "note": "补齐停用设备展示规则"})
execute = runner.execute
with patch.object(runner, "execute", capture):
    server.change(project, "step", {})
write("04-complete.json", server.snapshot(project))
