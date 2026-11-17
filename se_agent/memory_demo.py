"""Reproducible memory lifecycle demonstration, without model credentials."""
import json
import tempfile
from pathlib import Path
from se_agent.domain_memory import DomainMemory, MemorySession


def main():
    with tempfile.TemporaryDirectory() as directory:
        db=DomainMemory(str(Path(directory)/'memory.sqlite'))
        try:
            db.bootstrap('seats','lead')
            db.grant('seats','lead','author','member','A')
            db.grant('seats','lead','reviewer','reviewer','B')
            source=db.save('CASE-012','case','seats','author',dict(result='课堂方案走查',stage='walkthrough'),'published')
            body=dict(domain='校园预约',trigger='停用影响有效预约',correction='禁止新增，处理受影响预约并告知',pass_condition='走查已有预约的状态和通知',evidence_state='walkthrough',sources=[source],tags=['资源','停用','时段重叠'],required_tags=['资源','停用','时段重叠'],scope='有效预约时段重叠',exceptions='仅改名或时段不重叠不触发')
            draft=db.save('EXP-012','experience','seats','author',body,'project')
            approved=db.publish('seats','reviewer',draft,1,'独立核查案例来源与适用范围')
            db.bootstrap('devices','lead')
            db.grant('devices','lead','agent','member','A')
            db.freeze_baseline('devices','lead',[approved])
            db.alias('校园预约','周五送修','停用')
            session=MemorySession(db,'devices','agent','review')
            hits=session.search('校园预约',['资源','周五送修','时段重叠'])
            assert hits[0]['trigger_matched']
            db.checklist('devices','RUN-1','agent',approved,'pending','核对已有预约处置')
            result=db.commit_stage('devices','RUN-1','agent','evaluation-1', [dict(id='EVAL-1',kind='artifact',body={'finding':'应补充已有预约处置'})],dict(stage='review',completed=['EVAL-1'],pending=['human-confirm']),0)
            assert db.commit_stage('devices','RUN-1','agent','evaluation-1', [dict(id='EVAL-1',kind='artifact',body={'finding':'应补充已有预约处置'})],dict(stage='review',completed=['EVAL-1'],pending=['human-confirm']),0)==result
            print(json.dumps(dict(experience=approved,trigger=hits[0]['trigger_matched'],evidence=hits[0]['body']['evidence_state'],recovered=db.resume('devices','RUN-1','agent')),ensure_ascii=False,indent=2))
        finally:
            db.close()

if __name__ == '__main__':
    main()
