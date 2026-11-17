"""Create a versioned task DAG from a confirmed specification."""

def decompose(spec):
    source = f'{spec["id"]}@{spec["version"]}'
    rows = [
        ("T1", "定义接口与业务规则", (), "plan", "docs/rules.md", 5, False),
        ("T2", "实现主功能", ("T1",), "develop", "app/service.py", 10, True),
        ("T3", "整理使用说明", ("T1",), "docs", "docs/usage.md", 5, False),
        ("T4", "集成交付", ("T2", "T3"), "integrate", "docs/delivery.md", 5, False),
    ]
    tasks = [
        {"id": id_, "title": title, "depends_on": list(deps), "source": source,
         "role": role, "output": path, "write_paths": [path], "estimate_minutes": minutes,
         "review_required": review}
        for id_, title, deps, role, path, minutes, review in rows
    ]
    return {"id": "PLAN-01", "version": 1, "spec": source, "tasks": tasks, "max_parallel": 2}
