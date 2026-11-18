"""Tests for the unified replacement of the old unrestricted Memory API."""
import unittest
from se_agent.memory import Memory
from se_agent.store import Store
from se_agent.memory_migration import migrate

class UnifiedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store=Store()
        self.m=self.store.memory
        self.store.create_project('P')
        self.m.bootstrap('P','admin')
        self.m.grant('P','admin','a','member','A')

    def tearDown(self):
        self.store.close()

    def test_shared_connection_and_outer_rollback(self):
        self.assertIs(self.m.db,self.store.db)
        with self.assertRaises(RuntimeError):
            with self.store.tx():
                self.store.create_project('ROLLBACK')
                self.m.save('D','decision','P','a',{'choice':'one'})
                raise RuntimeError('simulate failure')
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM projects WHERE id='ROLLBACK'").fetchone())
        self.assertEqual(self.m.history('D','P','a','sharing'),[])

    def test_savepoint_failure_does_not_commit_or_destroy_outer_work(self):
        with self.store.tx():
            self.store.create_project('KEEP')
            try:
                with self.store.tx():
                    self.m.save('BAD','artifact','P','a',{})
                    raise ValueError('inner failed')
            except ValueError:
                pass
            self.m.save('GOOD','artifact','P','a',{})
        self.assertEqual(len(self.m.history('GOOD','P','a','sharing')),1)
        self.assertEqual(self.m.history('BAD','P','a','sharing'),[])
        self.m.close()
        self.assertIsNotNone(self.store.db.execute("SELECT 1 FROM projects WHERE id='KEEP'").fetchone())

    def test_legacy_adapter_fails_closed(self):
        with self.assertRaises(PermissionError):
            Memory(self.store).search('P')
        adapter=Memory(self.store,project_id='P',agent_id='a')
        with self.assertRaises(PermissionError):
            adapter.publish_experience({'publish_state':'published'})
        with self.assertRaises(PermissionError):
            adapter.search('OTHER')
        with self.assertRaises(PermissionError):
            adapter.search('P',stage='sharing')

    def test_migration_preserves_versions_and_blocks_second_writer(self):
        db=self.store.db
        for version in (1,2):
            db.execute('INSERT INTO experiences(experience_id,version,domain,symptom,trigger,correction,pass_condition,publish_state) VALUES(?,?,?,?,?,?,?,?)',('E',version,'d','s','t','c','p','published'))
        first=migrate(self.store)
        self.assertEqual(first['mapped'],2)
        self.assertEqual(migrate(self.store)['mapped'],0)
        self.assertEqual(db.execute('SELECT count(*) FROM experiences').fetchone()[0],2)
        self.assertEqual(db.execute('SELECT count(*) FROM dm_legacy_map').fetchone()[0],2)
        self.assertEqual(db.execute("SELECT count(*) FROM dm_status WHERE state='review'").fetchone()[0],2)
        self.assertEqual(self.m.list_records('P','a','sharing'),[])
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("DELETE FROM experiences")

    def test_specification_and_memory_are_one_business_transaction(self):
        spec=dict(id='SPEC',version=1,status='confirmed',markdown='rule',decisions=[],open_items=[])
        with self.assertRaises(RuntimeError):
            with self.store.tx():
                self.store.save_spec('P',spec)
                raise RuntimeError('business rollback')
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM specifications WHERE project_id='P'").fetchone())
        self.assertEqual(self.m.history('P:specification:1','P','a','sharing'),[])
        self.store.save_spec('P',spec)
        self.assertEqual(len(self.m.history('P:specification:1','P','a','sharing')),1)
