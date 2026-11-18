import type { CSSProperties } from 'react'
import type { Attempt } from '../types/v2'
import type { WorkbenchPlan } from '../types/workbench'
import { formatDuration, formatVersion, taskStatusText } from './presentation'
import { buildTimeline } from './timelineData'

const attemptLabels: Record<Attempt['state'], string> = {
  running: '执行中', succeeded: '成功', failed: '失败', input_required: '待补充输入',
}

export function GanttTimeline({ plan, startedAt, selectedId, onSelect }: {
  plan: WorkbenchPlan
  startedAt: string | null
  selectedId?: string
  onSelect: (id: string) => void
}) {
  const { rows, ticks, totalSeconds, hasActualPosition, hasActualDuration } = buildTimeline(plan, startedAt)
  const hasPlan = rows.some(({ planned }) => planned !== undefined)
  const hasAxis = hasPlan || hasActualPosition
  const percent = (seconds: number) => `${(seconds / totalSeconds) * 100}%`
  const track = (start: number, end: number | null): CSSProperties => ({
    left: percent(start),
    width: percent((end ?? start) - start),
  })

  return (
    <section className="gantt-view flow-card" aria-labelledby="gantt-title">
      <div className="flow-heading"><h3 id="gantt-title">甘特图</h3><span className="chip chip-blue">{formatVersion(plan.ref)}</span></div>
      <p className="flow-muted">{hasPlan ? `计划总时长：${formatDuration(plan.plannedSeconds)}` : '暂无计划排期。'}</p>
      {!hasActualDuration && <p className="flow-muted">暂无实际用时。</p>}
      {!hasActualPosition && hasActualDuration && <p className="flow-muted">缺少运行起点或有效时间，实际用时仅显示在任务行。</p>}
      {hasAxis && (
        <div className="gantt-legend">
          {hasPlan && <span>蓝色：计划</span>}
          {rows.some(({ planned }) => planned?.critical) && <span>深蓝色：关键路径</span>}
          {hasActualPosition && <span>绿色：实际尝试，红色：失败尝试，圆点：仅有开始时间</span>}
        </div>
      )}
      <div className={`gantt-scroll${hasAxis ? ' gantt-has-axis' : ''}`} role="region" aria-label="甘特图时间轴" tabIndex={0}>
        <div className="gantt-chart" style={{ '--gantt-step': `${100 / (ticks.length - 1)}%` } as CSSProperties}>
          {hasAxis && (
            <div className="gantt-axis">
              <span>任务 / 计划与实际</span>
              <div className="gantt-ticks" aria-label="分钟刻度">
                {ticks.map((minute) => <span key={minute} style={{ left: percent(minute * 60) }}>{minute} 分</span>)}
              </div>
            </div>
          )}
          {rows.length === 0 && <p className="flow-muted">暂无计划任务。</p>}
          {rows.map(({ task, planned, actual }) => (
            <button type="button" key={task.id} className="gantt-row"
              aria-pressed={task.id === selectedId} onClick={() => onSelect(task.id)}>
              <span className="gantt-lines gantt-label">
                <strong>{task.id} · {task.title} <small className="gantt-task-state">{taskStatusText[task.status]}</small></strong>
                <span>{planned ? `计划 ${formatDuration(planned.early_start_seconds)} → ${formatDuration(planned.early_finish_seconds)}` : '无计划排期'}
                  {planned?.critical && <b> · 关键路径</b>}</span>
                {actual.length === 0 ? <span>暂无实际执行记录</span> : actual.map(({ attempt, duration, start }) => (
                  <span key={attempt.id} className={attempt.state === 'failed' ? 'gantt-failure-label' : undefined}>
                    第 {attempt.number} 次 · {attemptLabels[attempt.state]} · {duration === null ? '实际时间不完整' : formatDuration(duration)}
                    {duration !== null && start === null && ' · 无法定位'}
                  </span>
                ))}
              </span>
              {hasAxis && (
                <span className="gantt-lines gantt-tracks" aria-hidden="true">
                  <span />
                  <span className="gantt-track">
                    {planned && <span className={`gantt-bar gantt-planned${planned.critical ? ' gantt-critical' : ''}`}
                      style={track(planned.early_start_seconds, planned.early_finish_seconds)} />}
                  </span>
                  {actual.map(({ attempt, start, end }) => (
                    <span className="gantt-track" key={attempt.id}>
                      {start !== null && <span className={`gantt-bar gantt-actual${attempt.state === 'failed' ? ' gantt-failed' : ''}${end === null ? ' gantt-point' : ''}`}
                        style={track(start, end)} />}
                    </span>
                  ))}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}
