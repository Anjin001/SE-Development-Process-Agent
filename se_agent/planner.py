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


def decompose_tasks(confirmed_spec):
    """Produce deterministic v2 tasks from confirmed structured decisions.

    Each requirement gets an independent implementation task. Documentation
    runs alongside implementation; integration joins all branches.
    """
    if confirmed_spec.get('status') != 'confirmed':
        raise ValueError('Specification must be confirmed')
    decisions = confirmed_spec['decisions']
    pending = [o for o in confirmed_spec['open_items'] if o['blocking'] and o['resolution'] is None]
    requirements = list(dict.fromkeys([r for d in decisions for r in d['requirement_ids']]
                                      + [r for o in pending for r in o['affected_requirement_ids']]))
    if not requirements:
        raise ValueError('Specification has no actionable requirement references')
    tasks = []
    def task(id_, title, refs, deps, phase, role, path, seconds, review):
        related = [d for d in decisions if set(refs) & set(d['requirement_ids'])]
        return dict(id=id_, title=title, phase=phase, requirement_ids=refs,
                    decision_ids=[d['id'] for d in related], open_item_ids=[],
                    depends_on=deps, role=role, write_paths=[path],
                    outputs=[dict(id='OUT-01', kind='text', path=path)],
                    estimate_seconds=seconds, review_required=review,
                    completion_condition=('逐条验证：' + '；'.join(d['choice'] for d in related)
                                          if related else '补齐未决输入后，按原需求逐条核验。'))
    names = {r['id']: r['title'] for r in confirmed_spec.get('requirement_items', [])}
    tasks.append(task('T1', '定义接口与业务规则', requirements, [], 'planning', 'plan', 'docs/rules.md', 300, False))
    for index, requirement in enumerate(requirements, 2):
        current = task(f'T{index}', f'实现 {requirement}' + (' · ' + names[requirement] if requirement in names else ''), [requirement], ['T1'], 'development', 'develop', f'app/task_{index}.py', 600, True)
        current['open_item_ids'] = [o['id'] for o in pending if requirement in o['affected_requirement_ids']]
        current['outputs'][0]['kind'] = 'git_commit'
        tasks.append(current)
    docs_id = f'T{len(tasks)+1}'
    tasks.append(task(docs_id, '整理使用说明', requirements, ['T1'], 'documentation', 'docs', 'docs/usage.md', 300, False))
    tasks.append(task(f'T{len(tasks)+1}', '集成验收', requirements, [t['id'] for t in tasks[1:]], 'integration', 'integrate', 'docs/delivery.md', 300, True))
    return tasks


def export_plan(plan, directory):
    """Export the orchestrator-owned Plan verbatim and readable task cards.

    Does not allocate versions, resolve dependencies or alter the supplied Plan.
    """
    import json
    from pathlib import Path
    snapshot = json.loads(json.dumps(plan, ensure_ascii=False, allow_nan=False))
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'workflow.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (destination / 'task.md').write_text(render_plan_markdown(snapshot), encoding='utf-8')
    return {'workflow': str(destination / 'workflow.json'), 'tasks': str(destination / 'task.md')}


def render_plan_markdown(snapshot):
    import json
    lines = ['# 任务计划', '', f"计划：{snapshot['id']}@{snapshot['version']}",
             f"规格：{snapshot['spec_ref']['id']}@{snapshot['spec_ref']['version']}",
             f"基线：{snapshot['base_commit']}", f"并发：{snapshot['max_parallel']}", '']
    for t in snapshot['tasks']:
        lines.extend([f"## {t['id']} · {t['title']}", ''])
        for key, value in t.items():
            lines.extend([f"- {key}: {json.dumps(value, ensure_ascii=False)}"])
        lines.append('')
    return '\n'.join(lines).rstrip()+'\n'
