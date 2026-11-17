# 任务计划

计划：PLAN-01@1
规格：SPEC-01@1
基线：None
并发：2

## T1 · 定义接口与业务规则

- id: "T1"
- title: "定义接口与业务规则"
- phase: "planning"
- requirement_ids: ["REQ-01", "REQ-02"]
- decision_ids: ["DEC-01"]
- open_item_ids: []
- depends_on: []
- role: "plan"
- write_paths: ["docs/rules.md"]
- outputs: [{"id": "OUT-01", "kind": "text", "path": "docs/rules.md"}]
- estimate_seconds: 300
- review_required: false
- completion_condition: "逐条验证并记录以下规则的验收证据：停用设备保留且显示状态，服务端拒绝新预约。"

## T2 · 实现 REQ-01

- id: "T2"
- title: "实现 REQ-01"
- phase: "development"
- requirement_ids: ["REQ-01"]
- decision_ids: ["DEC-01"]
- open_item_ids: []
- depends_on: ["T1"]
- role: "develop"
- write_paths: ["app/task_2.py"]
- outputs: [{"id": "OUT-01", "kind": "git_commit", "path": "app/task_2.py"}]
- estimate_seconds: 600
- review_required: true
- completion_condition: "逐条验证并记录以下规则的验收证据：停用设备保留且显示状态，服务端拒绝新预约。"

## T3 · 实现 REQ-02

- id: "T3"
- title: "实现 REQ-02"
- phase: "development"
- requirement_ids: ["REQ-02"]
- decision_ids: ["DEC-01"]
- open_item_ids: ["OPEN-01"]
- depends_on: ["T1"]
- role: "develop"
- write_paths: ["app/task_3.py"]
- outputs: [{"id": "OUT-01", "kind": "git_commit", "path": "app/task_3.py"}]
- estimate_seconds: 600
- review_required: true
- completion_condition: "逐条验证并记录以下规则的验收证据：停用设备保留且显示状态，服务端拒绝新预约。"

## T4 · 整理使用说明

- id: "T4"
- title: "整理使用说明"
- phase: "documentation"
- requirement_ids: ["REQ-01", "REQ-02"]
- decision_ids: ["DEC-01"]
- open_item_ids: []
- depends_on: ["T1"]
- role: "docs"
- write_paths: ["docs/usage.md"]
- outputs: [{"id": "OUT-01", "kind": "text", "path": "docs/usage.md"}]
- estimate_seconds: 300
- review_required: false
- completion_condition: "逐条验证并记录以下规则的验收证据：停用设备保留且显示状态，服务端拒绝新预约。"

## T5 · 集成验收

- id: "T5"
- title: "集成验收"
- phase: "integration"
- requirement_ids: ["REQ-01", "REQ-02"]
- decision_ids: ["DEC-01"]
- open_item_ids: []
- depends_on: ["T2", "T3", "T4"]
- role: "integrate"
- write_paths: ["docs/delivery.md"]
- outputs: [{"id": "OUT-01", "kind": "text", "path": "docs/delivery.md"}]
- estimate_seconds: 300
- review_required: true
- completion_condition: "逐条验证并记录以下规则的验收证据：停用设备保留且显示状态，服务端拒绝新预约。"
