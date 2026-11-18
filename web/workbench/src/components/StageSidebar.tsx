import { ArrowRight, Bot, CircleDot } from 'lucide-react'
import type { StageSummary, WorkbenchSnapshot } from '../types/workbench'
import { formatVersion, stageStatusText } from './presentation'
import { RunControls } from './RunControls'
import type { RunControlHandlers } from './RunControls'

function evidenceText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object' && 'id' in value && 'version' in value &&
      typeof value.id === 'string' && typeof value.version === 'number') {
    return formatVersion({ id: value.id, version: value.version })
  }
  return JSON.stringify(value) ?? '未知'
}

const severityLabels = { info: '信息', warning: '提醒', error: '异常' }

export function StageSidebar({ stage, snapshot, selectedId, onSelect, controls }: {
  stage: StageSummary
  snapshot: WorkbenchSnapshot
  selectedId?: string
  onSelect: (id: string) => void
  controls: RunControlHandlers
}) {
  const isWorkflow = stage.id === 'workflow'
  const isExecution = stage.id === 'execution'
  const { run, specificationRef, findings } = snapshot
  const plan = isWorkflow ? snapshot.latestPlan : snapshot.plan
  const stageFindings = findings.filter((finding) => (
    finding.stage === stage.id ||
    (stage.id === 'review' && finding.stage === 'specification') ||
    (isWorkflow && finding.stage === 'planning') ||
    (isExecution && finding.stage === 'completed')
  ))
  const status = isExecution && run?.dispatch === 'paused' && run.state === 'active'
    ? '派发已暂停' : stageStatusText[stage.status]
  const spec = plan?.specRef ?? specificationRef
  const planRef = plan?.ref ?? (isWorkflow ? snapshot.projectData.latest_plan_ref : run?.plan_ref)
  const references = [spec && formatVersion(spec), planRef && formatVersion(planRef), isExecution && run?.id].filter(Boolean)

  return (
    <aside className="stage-sidebar" aria-labelledby="sidebar-title">
      <div className="sidebar-heading"><CircleDot size={15} aria-hidden="true" /> {isWorkflow ? '阶段确认' : isExecution ? '运行控制' : '阶段信息'}</div>
      <div className="sidebar-card">
        <div className="sidebar-icon"><Bot size={22} aria-hidden="true" /></div>
        <h2 id="sidebar-title">{isWorkflow || isExecution ? `${stage.label}主理 AI` : stage.label}</h2>
        <span className="chip chip-blue">{status}</span>
        <p>{isWorkflow && stage.status === 'active' ? '请核对规格版本和计划用时。' : stage.description}</p>
        {stageFindings.length > 0 && (
          <ul className="run-findings">
            {stageFindings.map((finding, index) => (
              <li key={`${finding.code}:${index}`}>
                <strong>{severityLabels[finding.severity]} · {finding.summary}</strong>
                <p>证据：{finding.evidence_refs.length ? finding.evidence_refs.map(evidenceText).join('、') : '无'}</p>
                <p>影响任务：{finding.affected_task_ids.length ? finding.affected_task_ids.map((id) => (
                  <span key={id} className="finding-task">{plan?.tasks.some((task) => task.id === id)
                    ? <button className="task-link" type="button" onClick={() => onSelect(id)}>{id}</button> : id}</span>
                )) : '无'}</p>
                {finding.suggested_action && <p>建议：{finding.suggested_action}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>
      {(isWorkflow || isExecution) && (
        <div className="handoff">
          <h3>当前交接</h3>
          <p>
            {references.length === 0 ? '暂无关联版本' : references.map((reference, index) => (
              <span className="handoff-step" key={`${index}:${reference}`}>
                {index > 0 && <ArrowRight size={12} aria-label="流向" />} {reference}
              </span>
            ))}
          </p>
        </div>
      )}
      {(isWorkflow || isExecution) && (
        <RunControls snapshot={snapshot} execution={isExecution} selectedId={selectedId} onSelect={onSelect} handlers={controls} />
      )}
    </aside>
  )
}
