"""SQLite domain memory. Context identities must be supplied by trusted services."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class DomainMemory:
    def __init__(self, path=None, *, store=None):
        self.store = store
        self._owns_connection = store is None
        self.db = store.db if store is not None else sqlite3.connect(path, isolation_level=None)
        if self.db.in_transaction:
            raise RuntimeError('Initialize memory before entering business transactions')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS dm_members(project TEXT, agent TEXT, group_id TEXT,
          PRIMARY KEY(project,agent));
        CREATE TABLE IF NOT EXISTS dm_records(id TEXT, version INTEGER, kind TEXT,
          project TEXT, owner TEXT, group_id TEXT, visibility TEXT, body TEXT,
          PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS dm_baselines(project TEXT PRIMARY KEY, refs TEXT);
        CREATE TABLE IF NOT EXISTS dm_aliases(domain TEXT, alias TEXT, tag TEXT,
          PRIMARY KEY(domain,alias,tag));
        CREATE TABLE IF NOT EXISTS dm_checkpoints(project TEXT, run TEXT, revision INTEGER,
          body TEXT, PRIMARY KEY(project,run));
        CREATE TABLE IF NOT EXISTS dm_usage(project TEXT, run TEXT, agent TEXT,
          record_id TEXT, version INTEGER, body TEXT, created_at TEXT);
        ''')
        self._upgrade()

    def close(self):
        if self._owns_connection:
            self.db.close()

    @contextmanager
    def transaction(self):
        if self.store is not None:
            with self.store.tx():
                yield
            return
        if self.db.in_transaction:
            yield
            return
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def join(self, project, agent, group_id):
        self.db.execute('INSERT INTO dm_members VALUES(?,?,?) ON CONFLICT(project,agent) DO UPDATE SET group_id=excluded.group_id', (project, agent, group_id))

    def _member(self, project, agent):
        row = self.db.execute('SELECT group_id FROM dm_members WHERE project=? AND agent=?', (project, agent)).fetchone()
        if row is None:
            raise PermissionError('Not a project member')
        return row['group_id']

    def save(self, id, kind, project, owner, body, visibility='private', expected_version=0):
        """Append immutable content. Administrative service controls publication."""
        group = self._member(project, owner)
        if not isinstance(body, dict) or not isinstance(id, str) or not id.strip():
            raise ValueError('Record id and object body required')
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('Invalid expected version')
        json.dumps(body, allow_nan=False)
        if visibility not in ('private', 'group', 'project', 'published'):
            raise ValueError('Invalid visibility')
        if kind not in ('agent', 'message', 'artifact', 'decision', 'case', 'experience'):
            raise ValueError('Invalid record kind')
        if kind == 'experience':
            for key in ('domain', 'trigger', 'correction', 'pass_condition', 'evidence_state', 'sources'):
                if not body.get(key):
                    raise ValueError('Missing experience field: '+key)
            if body['evidence_state'] not in ('pending', 'simulated', 'walkthrough', 'pilot', 'production'):
                raise ValueError('Invalid evidence level')
            if visibility == 'published' and not body.get('reviewed_by'):
                raise ValueError('Publication requires reviewer')
        with self.transaction():
            previous = self.db.execute('SELECT * FROM dm_records WHERE id=? ORDER BY version DESC LIMIT 1', (id,)).fetchone()
            current = previous['version'] if previous else 0
            if current != expected_version:
                raise ValueError('VERSION_CONFLICT')
            if previous and (previous['project'], previous['owner'], previous['kind']) != (project, owner, kind):
                raise PermissionError('Record ownership mismatch')
            for ref in body.get('sources', []):
                source = self.read(ref['id'], ref['version'], project, owner, 'sharing')
                if visibility == 'published' and source['visibility'] != 'published':
                    raise PermissionError('Private source cannot be published indirectly')
            self.db.execute('INSERT INTO dm_records VALUES(?,?,?,?,?,?,?,?)', (id, current+1, kind, project, owner, group, visibility, json.dumps(body, ensure_ascii=False)))
        return {'id': id, 'version': current+1}

    def read(self, id, version, project, agent, stage):
        group = self._member(project, agent)
        if stage not in ('ideation','intake','race','review','group','sharing','integration','retrospective','execution'):
            raise ValueError('Unknown stage')
        if self.db.execute('SELECT 1 FROM dm_revoked WHERE id=?', (id,)).fetchone():
            raise PermissionError('Source revoked')
        row = self.db.execute('SELECT * FROM dm_records WHERE id=? AND version=?', (id, version)).fetchone()
        if row is None:
            raise KeyError(id)
        same = row['project'] == project
        allowed = row['visibility'] == 'published' or (same and row['owner'] == agent)
        if same and stage not in ('ideation', 'review', 'intake', 'race'):
            allowed |= row['visibility'] == 'group' and group is not None and group == row['group_id']
        if same and stage in ('sharing', 'integration', 'retrospective'):
            allowed |= row['visibility'] == 'project'
        if not allowed:
            raise PermissionError('Record is not visible')
        result = dict(row)
        result['body'] = json.loads(result['body'])
        # Re-check every source, including when original access has changed.
        for ref in result['body'].get('sources', []):
            self.read(ref['id'], ref['version'], project, agent, stage)
        return result

    def freeze_baseline(self, project, agent, refs):
        for ref in refs:
            record = self.read(ref['id'], ref['version'], project, agent, 'sharing')
            if record['visibility'] != 'published':
                raise PermissionError('Baseline requires published sources')
        self.db.execute('INSERT INTO dm_baselines VALUES(?,?)', (project, json.dumps(refs)))

    def alias(self, domain, alias, tag):
        self.db.execute('INSERT OR IGNORE INTO dm_aliases VALUES(?,?,?)', (domain, alias, tag))

    def search(self, project, agent, stage, domain, terms=(), limit=5):
        self._member(project, agent)
        if not 1 <= limit <= 50:
            raise ValueError('limit must be 1..50')
        baseline = self.db.execute('SELECT refs FROM dm_baselines WHERE project=?', (project,)).fetchone()
        if baseline is None:
            raise ValueError('Freeze project baseline before retrieval')
        expanded = set(terms)
        for term in terms:
            expanded.update(r['tag'] for r in self.db.execute('SELECT tag FROM dm_aliases WHERE domain=? AND alias=?', (domain, term)))
        results = []
        for ref in json.loads(baseline['refs']):
            try:
                record = self.read(ref['id'], ref['version'], project, agent, stage)
            except PermissionError:
                continue
            body = record['body']
            status = self.db.execute('SELECT state FROM dm_status WHERE id=? AND version=?', (record['id'], record['version'])).fetchone()
            if status and status['state'] != 'published':
                continue
            if record['kind'] != 'experience' or body['domain'] != domain or body.get('state', 'published') != 'published':
                continue
            hits = expanded & set(body.get('tags', []) + body.get('keywords', []))
            if not hits:
                continue
            required = set(body.get('required_tags', []))
            results.append(dict(record, matched=sorted(hits), trigger_matched=bool(required) and required <= expanded))
        rank = {'pending':0,'simulated':1,'walkthrough':2,'pilot':3,'production':4}
        return sorted(results, key=lambda r: (-int(r['trigger_matched']), -len(r['matched']), -rank[r['body']['evidence_state']], r['id'], r['version']))[:limit]

    def checkpoint(self, project, run, agent, body, expected_revision=0):
        self._member(project, agent)
        with self.transaction():
            row = self.db.execute('SELECT revision FROM dm_checkpoints WHERE project=? AND run=?', (project, run)).fetchone()
            revision = row['revision'] if row else 0
            if revision != expected_revision:
                raise ValueError('VERSION_CONFLICT')
            self.db.execute('INSERT INTO dm_checkpoints VALUES(?,?,?,?) ON CONFLICT(project,run) DO UPDATE SET revision=excluded.revision,body=excluded.body', (project, run, revision+1, json.dumps(body)))
        return revision+1

    def resume(self, project, run, agent):
        self._member(project, agent)
        row = self.db.execute('SELECT * FROM dm_checkpoints WHERE project=? AND run=?', (project, run)).fetchone()
        if row is None:
            raise KeyError(run)
        body = json.loads(row['body'])
        # Only controller state and references belong here; raw private chat stays in records.
        for ref in body.get('record_refs', []):
            self.read(ref['id'], ref['version'], project, agent, body['stage'])
        return {'revision': row['revision'], 'body': body}

    def usage(self, project, run, agent, ref, decision, reason, evidence=None):
        if decision not in ('adopted', 'declined') or not reason.strip():
            raise ValueError('Usage requires decision and reason')
        self.read(ref['id'], ref['version'], project, agent, 'sharing')
        self.db.execute('INSERT INTO dm_usage VALUES(?,?,?,?,?,?,?)', (project, run, agent, ref['id'], ref['version'], json.dumps(dict(decision=decision, reason=reason, evidence=evidence)), datetime.now(timezone.utc).isoformat()))

    def _upgrade(self):
        """Additive migration: never change the orchestrator's user_version."""
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS dm_roles(project TEXT,agent TEXT,role TEXT,
          PRIMARY KEY(project,agent));
        CREATE TABLE IF NOT EXISTS dm_status(id TEXT,version INTEGER,state TEXT,
          PRIMARY KEY(id,version), FOREIGN KEY(id,version) REFERENCES dm_records(id,version));
        CREATE TABLE IF NOT EXISTS dm_revoked(id TEXT PRIMARY KEY,reason TEXT);
        CREATE TABLE IF NOT EXISTS dm_audit(seq INTEGER PRIMARY KEY,project TEXT,
          actor TEXT,action TEXT,body TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS dm_operations(project TEXT,key TEXT,digest TEXT,result TEXT,
          PRIMARY KEY(project,key));
        CREATE TABLE IF NOT EXISTS dm_checks(project TEXT,run TEXT,id TEXT,version INTEGER,
          state TEXT,note TEXT,PRIMARY KEY(project,run,id,version));
        CREATE INDEX IF NOT EXISTS dm_records_project ON dm_records(project,kind);
        CREATE INDEX IF NOT EXISTS dm_usage_ref ON dm_usage(record_id,version);
        CREATE INDEX IF NOT EXISTS dm_audit_project ON dm_audit(project,seq);
        ''')

    def bootstrap(self, project, admin, group_id=None):
        """Trusted provisioning only; not exposed to agents or HTTP clients."""
        with self.transaction():
            if self.db.execute('SELECT 1 FROM dm_roles WHERE project=?', (project,)).fetchone():
                raise ValueError('Project already provisioned')
            self.join(project, admin, group_id)
            self.db.execute('INSERT INTO dm_roles VALUES(?,?,?)', (project,admin,'admin'))

    def grant(self, project, actor, agent, role, group_id=None):
        self._authorize(project, actor, ('admin',))
        if role not in ('member','reviewer','admin'):
            raise ValueError('Unknown role')
        with self.transaction():
            self.join(project, agent, group_id)
            self.db.execute('INSERT INTO dm_roles VALUES(?,?,?) ON CONFLICT(project,agent) DO UPDATE SET role=excluded.role', (project,agent,role))
            self._audit(project,actor,'grant',dict(agent=agent,role=role))

    def _authorize(self, project, actor, roles):
        row = self.db.execute('SELECT role FROM dm_roles WHERE project=? AND agent=?', (project,actor)).fetchone()
        if not row or row['role'] not in roles:
            raise PermissionError('Role does not permit this action')

    def _audit(self, project, actor, action, body):
        self.db.execute('INSERT INTO dm_audit(project,actor,action,body,created_at) VALUES(?,?,?,?,?)', (project,actor,action,json.dumps(body,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))

    def publish(self, project, reviewer, ref, expected_version, note):
        """Independent review; preserve candidate and publish a new version."""
        self._authorize(project,reviewer,('reviewer','admin'))
        with self.transaction():
            record = self.read(ref['id'],ref['version'],project,reviewer,'retrospective')
            if record['owner'] == reviewer or not note.strip():
                raise PermissionError('Independent review and note required')
            if record['version'] != expected_version:
                raise ValueError('VERSION_CONFLICT')
            body = dict(record['body'], reviewed_by=reviewer, review_note=note)
            result = self.save(record['id'],record['kind'],project,record['owner'],body,'published',expected_version)
            self.db.execute('INSERT INTO dm_status VALUES(?,?,?)', (result['id'],result['version'],'published'))
            self._audit(project,reviewer,'publish',dict(ref=result,note=note))
            return result

    def set_state(self, project, actor, ref, state, note):
        self._authorize(project,actor,('reviewer','admin'))
        if state not in ('published','review','retired','superseded') or not note.strip():
            raise ValueError('Invalid state or empty reason')
        with self.transaction():
            row=self.db.execute('SELECT project FROM dm_records WHERE id=? AND version=?',(ref['id'],ref['version'])).fetchone()
            if row is None or row['project'] != project:
                raise KeyError(ref['id'])
            self.db.execute('INSERT INTO dm_status VALUES(?,?,?) ON CONFLICT(id,version) DO UPDATE SET state=excluded.state',(ref['id'],ref['version'],state))
            self._audit(project,actor,'state',dict(ref=ref,state=state,note=note))

    def revoke(self, project, actor, id, reason):
        self._authorize(project,actor,('admin',))
        if not reason.strip():
            raise ValueError('Reason required')
        with self.transaction():
            row=self.db.execute('SELECT project FROM dm_records WHERE id=? LIMIT 1',(id,)).fetchone()
            if row is None or row['project'] != project:
                raise KeyError(id)
            self.db.execute('INSERT INTO dm_revoked VALUES(?,?) ON CONFLICT(id) DO UPDATE SET reason=excluded.reason',(id,reason))
            self._audit(project,actor,'revoke',dict(id=id,reason=reason))

    def commit_stage(self, project, run, actor, key, records, checkpoint, expected_revision):
        """One atomic, idempotent stage delivery, including artifacts and progress."""
        import hashlib
        self._member(project,actor)
        payload=dict(records=records,checkpoint=checkpoint,revision=expected_revision,actor=actor,run=run)
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if not key:
            raise ValueError('Idempotency key required')
        with self.transaction():
            previous=self.db.execute('SELECT * FROM dm_operations WHERE project=? AND key=?',(project,key)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                return json.loads(previous['result'])
            refs=[]
            for record in records:
                refs.append(self.save(project=project,owner=actor,**record))
            revision=self.checkpoint(project,run,actor,checkpoint,expected_revision)
            result=dict(refs=refs,revision=revision)
            self.db.execute('INSERT INTO dm_operations VALUES(?,?,?,?)',(project,key,digest,json.dumps(result)))
            self._audit(project,actor,'stage',dict(run=run,key=key,refs=refs,revision=revision))
            return result

    def context(self, project, agent, stage, refs, domain, terms=(), limit=5):
        """Rebuild allowed context every call; never reuse unfiltered chat history."""
        return dict(facts=[self.read(r['id'],r['version'],project,agent,stage) for r in refs],
                    experiences=self.search(project,agent,stage,domain,terms,limit))

    def history(self, id, project, agent, stage):
        versions=self.db.execute('SELECT version FROM dm_records WHERE id=? ORDER BY version',(id,)).fetchall()
        records=[]
        for row in versions:
            try:
                records.append(self.read(id,row['version'],project,agent,stage))
            except PermissionError:
                pass
        return records

    def checklist(self, project, run, agent, ref, state, note):
        self.read(ref['id'],ref['version'],project,agent,'sharing')
        if state not in ('pending','passed','not_applicable','failed') or not note.strip():
            raise ValueError('Invalid check result')
        with self.transaction():
            self.db.execute('INSERT INTO dm_checks VALUES(?,?,?,?,?,?) ON CONFLICT(project,run,id,version) DO UPDATE SET state=excluded.state,note=excluded.note',(project,run,ref['id'],ref['version'],state,note))
            self._audit(project,agent,'check',dict(run=run,ref=ref,state=state,note=note))

    def audit(self, project, actor, after=0):
        self._authorize(project,actor,('admin','reviewer'))
        return [dict(r) for r in self.db.execute('SELECT * FROM dm_audit WHERE project=? AND seq>? ORDER BY seq LIMIT 100',(project,after))]

    def backup(self, destination):
        """Trusted administration: consistent SQLite backup, including WAL data."""
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
        finally:
            target.close()

    def list_records(self, project, agent, stage, kind=None, limit=100):
        self._member(project, agent)
        if not 1 <= limit <= 1000:
            raise ValueError('Invalid limit')
        rows=self.db.execute('SELECT id,MAX(version) AS version FROM dm_records GROUP BY id ORDER BY id').fetchall()
        results=[]
        for row in rows:
            try:
                record=self.read(row['id'],row['version'],project,agent,stage)
            except PermissionError:
                continue
            if kind is None or record['kind'] == kind:
                results.append(record)
            if len(results) == limit:
                break
        return results


class MemorySession:
    """Identity-bound agent adapter. Construct only after server authentication.

    Administrative methods remain exclusively on DomainMemory. Agent tools get
    this object, never the database or caller-controlled project/identity/stage.
    """
    def __init__(self, memory, project, agent, stage):
        memory._member(project, agent)
        self._memory, self._project, self._agent, self._stage = memory, project, agent, stage

    def read(self, ref):
        return self._memory.read(ref['id'],ref['version'],self._project,self._agent,self._stage)

    def search(self, domain, terms=(), limit=5):
        return self._memory.search(self._project,self._agent,self._stage,domain,terms,limit)

    def propose(self, id, kind, body, expected_version=0):
        # Agents cannot manufacture a reviewer, publication or shared checkpoint.
        body=dict(body)
        body.pop('reviewed_by',None)
        body.pop('review_note',None)
        return self._memory.save(id,kind,self._project,self._agent,body,'private',expected_version)

# All shared-connection operations use Store's reentrant lock, including reads
# and compatibility helpers which do not themselves open a transaction.
def _store_locked(method):
    from functools import wraps
    @wraps(method)
    def guarded(self, *args, **kwargs):
        from contextlib import nullcontext
        with self.store._lock if self.store is not None else nullcontext():
            return method(self, *args, **kwargs)
    return guarded

for _name in ('join','save','read','freeze_baseline','alias','search','checkpoint',
              'resume','usage','bootstrap','grant','publish','set_state','revoke',
              'commit_stage','context','history','checklist','audit','backup','list_records'):
    setattr(DomainMemory, _name, _store_locked(getattr(DomainMemory, _name)))
