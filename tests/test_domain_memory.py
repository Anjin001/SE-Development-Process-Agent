import tempfile
import unittest
from pathlib import Path
from se_agent.domain_memory import DomainMemory

class DomainMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'memory.sqlite')
        self.m = DomainMemory(self.path)
        for p in ('P', 'Q'):
            self.m.join(p, 'A', 'A')
            self.m.join(p, 'B', 'B')

    def tearDown(self):
        self.m.close()
        self.tmp.cleanup()

    def card(self):
        source = self.m.save('CASE', 'case', 'P', 'A', {'result': 'classroom walkthrough'}, 'published')
        body = dict(domain='预约', trigger='停用影响已有预约', correction='处理预约并告知', pass_condition='记录处置与通知', evidence_state='walkthrough', sources=[source], reviewed_by='reviewer', tags=['资源', '停用', '重叠'], required_tags=['资源', '停用', '重叠'])
        return self.m.save('EXP', 'experience', 'P', 'A', body, 'published'), body

    def test_isolation_and_indirect_leak(self):
        ref = self.m.save('secret', 'artifact', 'P', 'B', {'text':'B draft'}, 'group')
        for stage in ('ideation', 'review', 'sharing'):
            with self.assertRaises(PermissionError):
                self.m.read('secret', 1, 'P', 'A', stage)
        with self.assertRaises(PermissionError):
            self.m.save('leak', 'artifact', 'P', 'B', {'sources':[ref]}, 'published')
        with self.assertRaises(PermissionError):
            self.m.read('secret', 1, 'Q', 'B', 'sharing')

    def test_baseline_versions_alias_and_boundary(self):
        ref, body = self.card()
        self.m.freeze_baseline('Q', 'A', [ref])
        self.m.alias('预约', '送修', '停用')
        hits = self.m.search('Q','B','review','预约',['资源','送修','重叠'])
        self.assertTrue(hits[0]['trigger_matched'])
        self.assertEqual(hits[0]['body']['evidence_state'], 'walkthrough')
        self.assertFalse(self.m.search('Q','B','review','预约',['资源','改名'])[0]['trigger_matched'])
        body['correction'] = 'new rule'
        self.m.save('EXP','experience','P','A',body,'published',1)
        self.assertEqual(self.m.search('Q','B','review','预约',['资源'])[0]['version'],1)
        with self.assertRaises(Exception):
            self.m.freeze_baseline('Q','A',[ref])
        self.m.usage('Q','RUN','B',ref,'declined','时段不重叠')
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM dm_usage').fetchone()[0],1)

    def test_restart_and_checkpoint_conflict(self):
        self.m.checkpoint('P','RUN','A',{'stage':'review','completed':['score-A'],'pending':['score-B']})
        self.m.close()
        self.m = DomainMemory(self.path)
        state = self.m.resume('P','RUN','A')
        self.assertEqual(state['body']['pending'],['score-B'])
        with self.assertRaises(ValueError):
            self.m.checkpoint('P','RUN','A',{},0)
        with self.assertRaises(PermissionError):
            self.m.resume('P','RUN','outsider')

    def test_atomic_failure_and_version_conflict(self):
        self.m.save('D','decision','P','A',{'choice':'first'})
        with self.assertRaises(ValueError):
            self.m.save('D','decision','P','A',{'choice':'overwrite'})
        with self.assertRaises(KeyError):
            self.m.save('bad','artifact','P','A',{'sources':[{'id':'missing','version':1}]})
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM dm_records WHERE id='bad'").fetchone()[0],0)
