"""Explicit, repeatable migration of ZXQ legacy memory into the unified store.

Legacy tables are retained and become read-only after successful migration.
Unreviewed legacy access never silently becomes cross-project publication.
"""
import argparse
import json
from .store import Store

LEGACY_TABLES = ('agents','project_members','project_cases','experiences',
                 'experience_tags','experience_keywords','tag_aliases','memory_sources',
                 'memory_usage','messages','issues','checkpoints')


def migrate(store, backup_path=None):
    m=store.memory
    if backup_path:
        m.backup(backup_path)
    store.db.executescript('''
    CREATE TABLE IF NOT EXISTS dm_legacy_archive(table_name TEXT,row_key TEXT,body TEXT,
       PRIMARY KEY(table_name,row_key));
    CREATE TABLE IF NOT EXISTS dm_legacy_map(old_id TEXT,old_version INTEGER,
       new_id TEXT,new_version INTEGER,quarantined INTEGER,reason TEXT,
       PRIMARY KEY(old_id,old_version));
    ''')
    report={'archived':0,'mapped':0,'quarantined':0}
    with store.tx() as db:
        # Archive every legacy row, including raw relationships and usage history.
        for table in LEGACY_TABLES:
            for row in db.execute('SELECT rowid AS _migration_rowid,* FROM '+table).fetchall():
                body=dict(row)
                key=str(body.pop('_migration_rowid'))
                db.execute('INSERT OR IGNORE INTO dm_legacy_archive VALUES(?,?,?)',
                           (table,key,json.dumps(body,ensure_ascii=False)))
                report['archived']+=1
        for row in db.execute('SELECT * FROM experiences ORDER BY experience_id,version').fetchall():
            old=dict(row)
            if db.execute('SELECT 1 FROM dm_legacy_map WHERE old_id=? AND old_version=?',(old['experience_id'],old['version'])).fetchone():
                continue
            refs=[dict(r) for r in db.execute('SELECT * FROM memory_sources WHERE experience_id=? AND version=?',(old['experience_id'],old['version']))]
            project=next((r['source_project'] for r in refs if r['source_project']),None) or '__legacy_quarantine__'
            owner='__legacy_importer__'
            m.join(project,owner,None)
            id=old['experience_id']
            collision=db.execute('SELECT 1 FROM dm_records WHERE id=?',(id,)).fetchone()
            earlier=db.execute('SELECT new_id FROM dm_legacy_map WHERE old_id=? LIMIT 1',(id,)).fetchone()
            if earlier:
                id=earlier['new_id']
            elif collision:
                from uuid import uuid4
                id='legacy-'+id+'-'+uuid4().hex
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM dm_records WHERE id=?',(id,)).fetchone()[0]
            tags=[r['tag'] for r in db.execute('SELECT tag FROM experience_tags WHERE experience_id=? AND version=?',(old['experience_id'],old['version']))]
            keywords=[r['phrase'] for r in db.execute('SELECT phrase FROM experience_keywords WHERE experience_id=? AND version=?',(old['experience_id'],old['version']))]
            # Preserve claims but require fresh independent review and real source links.
            body=dict(old,tags=tags,keywords=keywords,sources=[],legacy_sources=refs,
                      migration_reason='Legacy visibility/source permissions need explicit review')
            db.execute('INSERT INTO dm_records VALUES(?,?,?,?,?,?,?,?)',(id,version,'experience',project,owner,None,'private',json.dumps(body,ensure_ascii=False)))
            db.execute('INSERT INTO dm_status VALUES(?,?,?)',(id,version,'review'))
            db.execute('INSERT INTO dm_legacy_map VALUES(?,?,?,?,?,?)',(old['experience_id'],old['version'],id,version,1,body['migration_reason']))
            report['mapped']+=1;report['quarantined']+=1
        # Prevent any forgotten caller from continuing to populate a second truth.
        for table in LEGACY_TABLES:
            for operation in ('INSERT','UPDATE','DELETE'):
                name='dm_legacy_readonly_'+table+'_'+operation.lower()
                db.execute(f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Legacy memory is read-only; use Store.memory'); END")
        m._audit('__migration__','system','legacy-migration',report)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--backup',required=True)
    args=parser.parse_args()
    import os
    if os.path.abspath(args.db)==os.path.abspath(args.backup):
        raise ValueError('Backup must use a different path')
    if os.path.exists(args.backup):
        raise ValueError('Use a new backup filename')
    with Store(args.db) as store:
        print(json.dumps(migrate(store,args.backup),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
