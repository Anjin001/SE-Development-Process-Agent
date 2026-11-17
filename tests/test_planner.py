import copy
import json
import tempfile
import unittest
from pathlib import Path
from se_agent.planner import decompose_tasks, export_plan

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(Path('examples/v2/scenario.json').read_text(encoding='utf-8'))['project_snapshot']['specification']

    def test_confirmation(self):
        self.spec['status'] = 'draft'
        with self.assertRaises(ValueError):
            decompose_tasks(self.spec)

    def test_local_input_and_join(self):
        original = copy.deepcopy(self.spec)
        tasks = decompose_tasks(self.spec)
        self.assertEqual(original, self.spec)
        affected = [t for t in tasks if 'OPEN-01' in t['open_item_ids']]
        self.assertEqual(len(affected), 1)
        self.assertEqual(affected[0]['requirement_ids'], ['REQ-02'])
        docs = next(t for t in tasks if t['role'] == 'docs')
        self.assertEqual(docs['open_item_ids'], [])
        self.assertEqual(docs['depends_on'], ['T1'])
        self.assertIn(affected[0]['id'], tasks[-1]['depends_on'])
        self.assertIn(docs['id'], tasks[-1]['depends_on'])

    def test_export(self):
        plan = dict(id='P', version=7, spec_ref={'id': 'SPEC-01', 'version': 1}, base_commit=None, max_parallel=3, tasks=decompose_tasks(self.spec))
        plan['tasks'][1]['estimate_seconds'] = 901
        with tempfile.TemporaryDirectory() as directory:
            export_plan(plan, directory)
            self.assertEqual(json.loads((Path(directory)/'workflow.json').read_text(encoding='utf-8')), plan)
            cards = (Path(directory)/'task.md').read_text(encoding='utf-8')
            self.assertIn('901', cards)
            for task in plan['tasks']:
                self.assertIn(task['completion_condition'], cards)
