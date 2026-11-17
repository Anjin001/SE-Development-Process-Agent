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

class ExtendedMemoryTest(unittest.TestCase):
    setUp = DomainMemoryTest.setUp
    tearDown = DomainMemoryTest.tearDown
    card = DomainMemoryTest.card
    def provision(self):
        self.m.bootstrap('P','ADMIN')
        self.m.grant('P','ADMIN','A','member','A')
        self.m.grant('P','ADMIN','B','reviewer','B')

    def test_review_roles_and_revocation(self):
        self.provision()
        source=self.m.save('SOURCE','case','P','A',{'result':'simulation'},'published')
        body=dict(domain='d',trigger='t',correction='c',pass_condition='p',evidence_state='simulated',sources=[source],tags=['t'])
        ref=self.m.save('E','experience','P','A',body,'project')
        with self.assertRaises(PermissionError):
            self.m.publish('P','A',ref,1,'self approval')
        published=self.m.publish('P','B',ref,1,'sources checked')
        self.m.freeze_baseline('Q','A',[published])
        self.assertEqual(len(self.m.search('Q','A','review','d',['t'])),1)
        self.m.set_state('P','B',published,'retired','counterexample')
        self.assertEqual(self.m.search('Q','A','review','d',['t']),[])
        self.assertEqual(len(self.m.history('E','P','A','sharing')),2)
        self.m.revoke('P','ADMIN','SOURCE','withdrawn evidence')
        with self.assertRaises(PermissionError):
            self.m.read('E',2,'Q','A','sharing')
        self.assertTrue(self.m.audit('P','ADMIN'))

    def test_stage_idempotency_and_rollback(self):
        records=[dict(id='stage-draft',kind='artifact',body={'text':'draft'})]
        body={'stage':'review','completed':['draft'],'pending':['evaluation']}
        first=self.m.commit_stage('P','RUN','A','key',records,body,0)
        self.assertEqual(first,self.m.commit_stage('P','RUN','A','key',records,body,0))
        with self.assertRaises(ValueError):
            self.m.commit_stage('P','RUN','A','key',records,{},0)
        with self.assertRaises(ValueError):
            self.m.commit_stage('P','RUN','A','new',[dict(id='rollback',kind='artifact',body={})],body,0)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM dm_records WHERE id='rollback'").fetchone()[0],0)
        self.assertEqual(self.m.resume('P','RUN','A')['revision'],1)

    def test_bound_adapter_and_backup(self):
        from se_agent.domain_memory import MemorySession
        session=MemorySession(self.m,'P','A','ideation')
        ref=session.propose('private','artifact',{'text':'mine','reviewed_by':'fake'})
        self.assertNotIn('reviewed_by',session.read(ref)['body'])
        with self.assertRaises(PermissionError):
            MemorySession(self.m,'P','B','sharing').read(ref)
        destination=str(Path(self.tmp.name)/'backup.sqlite')
        self.m.backup(destination)
        restored=DomainMemory(destination)
        try:
            self.assertEqual(restored.read('private',1,'P','A','ideation')['body']['text'],'mine')
        finally:
            restored.close()

    def test_two_connections_reject_stale_version(self):
        other=DomainMemory(self.path)
        try:
            self.m.save('concurrent','decision','P','A',{'choice':'one'})
            with self.assertRaises(ValueError):
                other.save('concurrent','decision','P','A',{'choice':'two'},expected_version=0)
        finally:
            other.close()

class MemoryBrowserTest(unittest.TestCase):
    def test_http_identity_cannot_be_overridden(self):
        import json
        import threading
        from urllib.request import urlopen
        from urllib.error import HTTPError
        from se_agent.memory_server import make_server
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'memory.sqlite')
            db=DomainMemory(path)
            db.join('P','A','A'); db.join('P','B','B')
            db.save('secret','message','P','B',{'text':'B secret'})
            db.close()
            server=make_server(path,'P','A','review',0)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                root=f'http://127.0.0.1:{server.server_port}'
                with urlopen(root+'/api/memory/records?agent=B') as response:
                    self.assertEqual(json.load(response)['items'],[])
                with self.assertRaises(HTTPError) as error:
                    urlopen(root+'/api/memory/record?id=secret&version=1&agent=B')
                self.assertEqual(error.exception.code,403)
            finally:
                server.shutdown(); thread.join(); server.server_close()
