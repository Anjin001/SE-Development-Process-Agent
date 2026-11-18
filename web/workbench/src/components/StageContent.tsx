import { CalendarDays, Clock3, FileCheck2, GitBranch, Layers3, PanelTop, SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { sameVersion } from '../api/projectVersions'
import type { StageSummary, WorkbenchPlan, WorkbenchSnapshot, WorkbenchView } from '../types/workbench'
import { DependencyPreview } from './DependencyPreview'
import { DeliveryReview } from './DeliveryReview'
import type { RunCommand } from '../types/v2'
import { GanttTimeline } from './GanttTimeline'
import { TaskConfiguration } from './TaskConfiguration'
import type { PlanEditor } from './TaskConfiguration'
import { TaskDetails } from './TaskDetails'
import { TaskList } from './TaskList'
import { formatDuration, formatVersion } from './presentation'

const views = {
  dependencies: { label: '依赖图', icon: GitBranch, description: '暂无可显示的计划任务。' },
  timeline: { label: '甘特图', icon: CalendarDays, description: '暂无可显示的计划。' },
  configuration: { label: '任务配置', icon: SlidersHorizontal, description: '暂无可配置的计划。' },
  delivery: { label: '交付审阅', icon: FileCheck2, description: '查看原始产物、人工修订和审核记录。' },
}

const workflowViews: readonly WorkbenchView[] = ['dependencies', 'timeline', 'configuration']
const executionViews: readonly WorkbenchView[] = [...workflowViews, 'delivery']

function Placeholder({ title, description, empty = false }: { title: string; description: string; empty?: boolean }) {
  return (
    <section className="placeholder">
      <span className="placeholder-icon"><PanelTop size={22} aria-hidden="true" /></span>
      <h3>{title}</h3>
      <p>{description}</p>
      {!empty && <span className="chip chip-waiting">待接入</span>}
    </section>
  )
}

export function StageContent({ stage, plan, latestPlan, run, selectedId, planEditor, submitReview, onSelect, children, notice, onRefresh }: {
  stage: StageSummary
  plan: WorkbenchPlan | null
  latestPlan: WorkbenchPlan | null
  run: WorkbenchSnapshot['run']
  selectedId?: string
  planEditor: PlanEditor
  submitReview?: (command: RunCommand) => Promise<boolean>
  onSelect: (id: string) => void
  children?: ReactNode
  notice?: string
  onRefresh?: () => void
}) {
  const [view, setView] = useState<WorkbenchView>('dependencies')
  const availableViews = stage.id === 'workflow' ? workflowViews : stage.id === 'execution' ? executionViews : []
  const visiblePlan = view === 'configuration' ? latestPlan : plan
  const task = visiblePlan?.tasks.find(({ id }) => id === selectedId) ?? visiblePlan?.tasks[0]
  const runMatchesPlan = run && plan && sameVersion(run.plan_ref, plan.ref)
  const showTaskPanel = visiblePlan && task
  const emptyView = !plan || (view === 'dependencies' && !task)

  return (
    <main className="stage-content" aria-labelledby="stage-title">
      <div className="stage-intro">
        <h2 id="stage-title">{stage.label}</h2>
        <p>{stage.description}</p>
      </div>
      {notice && <p className="flow-alert" role="alert">{notice}</p>}
      {onRefresh && <button className="run-button" type="button" disabled={planEditor.busy} onClick={onRefresh}>刷新快照</button>}
      {availableViews.length > 0 ? (
        <>
          <div className="view-switcher" role="group" aria-label="工作台视图">
            {availableViews.map((id) => {
              const { icon: Icon, label } = views[id]
              return (
                <button type="button" key={id} aria-pressed={view === id} onClick={() => setView(id)}>
                  <Icon size={15} aria-hidden="true" /> {label}
                </button>
              )
            })}
          </div>
          {visiblePlan && (
            <div className="plan-summary">
              <span><Layers3 size={15} aria-hidden="true" />
                {stage.id === 'execution' && view !== 'configuration' && runMatchesPlan ? '运行绑定 ' : '计划版本 '}{formatVersion(visiblePlan.ref)}
                <span className="summary-dot">·</span> {visiblePlan.tasks.length} 项任务 <span className="summary-dot">·</span> 并发 {visiblePlan.concurrency}</span>
              <strong><Clock3 size={15} aria-hidden="true" />
                {visiblePlan.plannedSeconds === null ? '暂无排期' : `计划 ${formatDuration(visiblePlan.plannedSeconds)}`}</strong>
            </div>
          )}
          {view === 'dependencies' && plan && task && <DependencyPreview tasks={plan.tasks} selectedId={task.id} onSelect={onSelect} />}
          {view === 'timeline' && plan && <GanttTimeline plan={plan} startedAt={runMatchesPlan ? run.started_at : null}
            selectedId={task?.id} onSelect={onSelect} />}
          {stage.id === 'execution' && <div hidden={view !== 'delivery'}>
            <DeliveryReview run={run} selectedId={task?.id} onSelect={onSelect} submit={submitReview} busy={planEditor.busy} />
          </div>}
          {emptyView && view !== 'configuration' && view !== 'delivery' && (
            <Placeholder title={views[view].label} description={views[view].description} empty />
          )}
          {view === 'configuration' && !showTaskPanel && (
            <TaskConfiguration plan={latestPlan?.source.plan ?? null} runPlan={run?.plan_ref ?? null} selectedId={selectedId} editor={planEditor} />
          )}
          {showTaskPanel && (
            <div className="workflow-task-panel">
              <TaskList tasks={visiblePlan.tasks} selectedId={task.id} onSelect={onSelect} />
              <div className="workflow-task-main">
                {view === 'configuration' && (
                  <TaskConfiguration plan={visiblePlan.source.plan} runPlan={run?.plan_ref ?? null}
                    selectedId={selectedId ?? task.id} editor={planEditor} />
                )}
                <TaskDetails task={task} tasks={visiblePlan.tasks} specRef={visiblePlan.specRef} onSelect={onSelect} />
              </div>
            </div>
          )}
        </>
      ) : children ?? <Placeholder title="阶段内容预留" description={stage.description} />}
    </main>
  )
}
