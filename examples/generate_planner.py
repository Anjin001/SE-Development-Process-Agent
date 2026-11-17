"""Generate task-planner handoff examples from the shared confirmed fixture."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from se_agent.planner import decompose_tasks, export_plan
root = Path(__file__).resolve().parent
spec = json.loads((root/'v2/scenario.json').read_text(encoding='utf-8'))['project_snapshot']['specification']
plan = dict(id='PLAN-01', version=1, spec_ref={'id': spec['id'], 'version': spec['version']}, base_commit=None, max_parallel=2, tasks=decompose_tasks(spec))
export_plan(plan, root/'planner')
