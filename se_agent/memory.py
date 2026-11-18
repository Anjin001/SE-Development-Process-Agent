"""Deprecated ZXQ entry point, now an identity-bound adapter to Store.memory.

Missing identity fails closed. Historical unrestricted reads/publication are
intentionally not preserved: the caller must authenticate and review explicitly.
"""
from .domain_memory import MemorySession


class Memory:
    def __init__(self, store, *, project_id=None, agent_id=None, stage='review'):
        self.store = store
        self.service = store.memory
        self.project_id, self.agent_id, self.stage = project_id, agent_id, stage

    def _session(self, project_id=None):
        if self.project_id is None or self.agent_id is None:
            raise PermissionError('Bind authenticated project_id and agent_id')
        if project_id is not None and project_id != self.project_id:
            raise PermissionError('Project mismatch')
        return MemorySession(self.service,self.project_id,self.agent_id,self.stage)

    def search(self, project_id=None, objects=(), operations=(), states=(), text='', domain=None, stage=None, limit=5, include_retired=False):
        session=self._session(project_id)
        if stage is not None and stage != self.stage:
            raise PermissionError('Stage is bound by the server')
        if include_retired:
            raise ValueError('Use authorized history for retired records')
        terms=list(objects)+list(operations)+list(states)
        if text:
            terms.extend(text.split())
        return session.search(domain,terms,limit)

    def history(self, experience_id):
        self._session()
        return self.service.history(experience_id,self.project_id,self.agent_id,self.stage)

    def publish_experience(self, card, origin_project=None, sources=()):
        self._session(origin_project)
        if card.get('publish_state','candidate') != 'candidate':
            raise PermissionError('Submit candidate, then call authorized service.publish')
        id=card.get('experience_id')
        if not id:
            from uuid import uuid4
            id='EXP-'+uuid4().hex
        body=dict(card,sources=list(sources))
        body['tags']=list(card.get('objects',()))+list(card.get('operations',()))+list(card.get('states',()))
        return self._session().propose(id,'experience',body,card.get('expected_version',0))

    def record_usage(self, run_id, agent_id, experience_id, version, decision, reason='', **evidence):
        self._session()
        if agent_id != self.agent_id:
            raise PermissionError('Agent mismatch')
        return self.service.usage(self.project_id,run_id,self.agent_id,dict(id=experience_id,version=version),decision,reason,evidence)
