"""SQLite domain memory. Context identities must be supplied by trusted services."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class DomainMemory:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
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

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
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
            if record['kind'] != 'experience' or body['domain'] != domain or body.get('state', 'published') != 'published':
                continue
            hits = expanded & set(body.get('tags', []) + body.get('keywords', []))
            if not hits:
                continue
            required = set(body.get('required_tags', []))
            results.append(dict(record, matched=sorted(hits), trigger_matched=bool(required) and required <= expanded))
        return sorted(results, key=lambda r: (-len(r['matched']), r['id'], r['version']))[:limit]

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
