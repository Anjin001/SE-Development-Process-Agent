import { useState } from 'react'
import { currentSpecification, sameVersion } from '../api/projectVersions'
import type { Run, RunCommand, RunTask, StartRunRequest } from '../types/v2'
import type { WorkbenchSnapshot } from '../types/workbench'
import { taskStatusText } from './presentation'

export interface RunControlHandlers {
  busy: boolean
  refresh: () => void
  start?: (body: StartRunRequest) => Promise<boolean>
  act?: (body: RunCommand) => Promise<boolean>
  loadDemo?: () => void
  openExecution: () => void
  sync: { ok: boolean; message: string } | null
}

function MissingInputs({ task, questions, busy, onSubmit }: {
  task: RunTask
  questions: ReadonlyMap<string, string>
  busy: boolean
  onSubmit?: (values: Record<string, string>) => Promise<boolean>
}) {
  const [draft, setDraft] = useState<ReadonlyMap<string, string>>(new Map())
  const fields = task.missing_input_ids
  const values = Object.fromEntries(fields.flatMap((id) => {
    const value = draft.get(id)?.trim()
    return value ? [[id, value]] : []
  }))
  return (
    <form className="run-input-form" onSubmit={async (event) => {
      event.preventDefault()
      if (busy || !onSubmit || Object.keys(values).length === 0) return
      if (await onSubmit(values)) setDraft(new Map())
    }}>
      <h3>补充输入 · {task.task_id}</h3>
      {fields.length ? (
        <>
          <p className="flow-muted">只提交已填写的当前缺项，可以分次提交。</p>
          {fields.map((id) => (
            <label key={id}>
              <strong>{id}</strong>{questions.get(id) && <span>{questions.get(id)}</span>}
              <textarea rows={2} value={draft.get(id) ?? ''} disabled={busy || !onSubmit}
                onChange={(event) => {
                  const value = event.target.value
                  setDraft((previous) => new Map(previous).set(id, value))
                }} />
            </label>
          ))}
          <button className="run-button run-primary" type="submit" disabled={!onSubmit || busy || Object.keys(values).length === 0}>提交补充输入</button>
        </>
      ) : <p className="flow-muted">快照没有列出可填写的缺项，请刷新核对。</p>}
    </form>
  )
}

export function RunControls({ snapshot, execution, selectedId, onSelect, handlers }: {
  snapshot: WorkbenchSnapshot
  execution: boolean
  selectedId?: string
  onSelect: (id: string) => void
  handlers: RunControlHandlers
}) {
  const [mode, setMode] = useState<Run['mode']>('manual')
  const { run, latestPlan, projectData } = snapshot
  const { busy, refresh, start, act, loadDemo, openExecution, sync } = handlers
  const active = run?.state === 'active'
  const spec = currentSpecification(projectData)
  const hasActive = active || projectData.runs.some(({ state }) => state === 'active')
  const canStart = latestPlan && spec?.status === 'confirmed' && sameVersion(spec, latestPlan.specRef) &&
    sameVersion(latestPlan.ref, projectData.latest_plan_ref) && !hasActive
  const selected = run?.tasks.find(({ task_id }) => task_id === selectedId)
  const tracked = run?.tasks.filter(({ state }) => ['failed', 'input_required', 'review', 'blocked'].includes(state)) ?? []
  const done = run?.tasks.filter(({ state }) => state === 'done').length ?? 0
  const questions = new Map<string, string>()
  if (spec && sameVersion(spec, snapshot.plan?.specRef ?? null)) {
    for (const item of spec.open_items) questions.set(item.id, item.question)
  }
  // New missing inputs may come from a reported attempt instead of the specification.
  for (const attempt of selected?.attempts ?? []) {
    const current = attempt.error?.details.questions
    if (current && typeof current === 'object' && !Array.isArray(current)) {
      for (const [id, question] of Object.entries(current)) if (typeof question === 'string') questions.set(id, question)
    }
  }

  async function issue(action: 'pause' | 'resume' | 'step') {
    if (run && act && active && !busy) await act({ action, expected_revision: run.revision })
  }

  return (
    <section className="run-controls" aria-labelledby="run-controls-title">
      <h3 id="run-controls-title">执行控制</h3>
      {(!hasActive || !execution) && (
        <div className="run-start">
          {!hasActive && (
            <label>运行模式
              <select value={mode} disabled={busy || !canStart || !start}
                onChange={(event) => setMode(event.target.value === 'auto' ? 'auto' : 'manual')}>
                <option value="manual">手动（单步）</option>
                <option value="auto">自动派发</option>
              </select>
            </label>
          )}
          <button className="run-button run-primary" type="button" disabled={busy || !start || !canStart}
            onClick={() => { if (start && canStart) void start({ plan_ref: latestPlan.ref, mode }) }}>确认工作区并启动</button>
          {hasActive ? (
            <button className="run-button" type="button" onClick={openExecution}>查看当前运行</button>
          ) : !canStart && <p className="flow-muted">需要已确认规格绑定的最新计划。</p>}
        </div>
      )}
      {execution && run && (
        <div className="run-status">
          <dl>
            <div><dt>模式</dt><dd>{run.mode === 'manual' ? '手动' : '自动'}</dd></div>
            <div><dt>状态</dt><dd>{run.state === 'stopped' ? '已停止' : run.state === 'completed' ? '已完成' : run.dispatch === 'paused' ? '派发已暂停' : '运行中'}</dd></div>
            <div><dt>修订号</dt><dd>{run.revision}</dd></div>
          </dl>
          <p className="run-count">{done} / {run.tasks.length} 项已交付</p>
          {run.tasks.length > 0 && <progress value={done} max={run.tasks.length} aria-label="已交付任务进度" />}
          {active && (
            <div className="run-buttons">
              {run.dispatch === 'active' ? (
                <button className="run-button" type="button" disabled={!act || busy} onClick={() => void issue('pause')}>暂停派发</button>
              ) : (
                <button className="run-button run-primary" type="button" disabled={!act || busy} onClick={() => void issue('resume')}>继续派发</button>
              )}
              {run.mode === 'manual' && (
                <button className="run-button" type="button"
                  disabled={!act || busy || run.dispatch !== 'active' || !run.tasks.some(({ state }) => state === 'ready')}
                  onClick={() => void issue('step')}>单步派发一批</button>
              )}
            </div>
          )}
          {active && run.dispatch === 'paused' && <p className="flow-muted">暂停期间仍接收已启动任务的回报。</p>}
          {tracked.length > 0 && (
            <ul className="run-tracked" aria-label="需关注的任务">
              {tracked.map((task) => (
                <li key={task.task_id}>
                  <button type="button" className="task-link" aria-pressed={task.task_id === selectedId}
                    onClick={() => onSelect(task.task_id)}>{task.task_id} · {taskStatusText[task.state]}</button>
                </li>
              ))}
            </ul>
          )}
          {active && selected?.state === 'failed' && (
            <button className="run-button run-primary" type="button" disabled={!act || busy}
              onClick={() => act && void act({ action: 'retry', task_id: selected.task_id, expected_revision: run.revision })}>
              重试失败任务 {selected.task_id}
            </button>
          )}
          {active && selected?.state === 'input_required' && (
            <MissingInputs key={JSON.stringify([run.id, selected.task_id])} task={selected} questions={questions} busy={busy}
              onSubmit={act && ((values) => act({ action: 'supply-input', task_id: selected.task_id, values, expected_revision: run.revision }))} />
          )}
          {sync && <p className={sync.ok ? 'flow-muted' : 'flow-alert'} role={sync.ok ? 'status' : 'alert'}>
            {sync.ok ? sync.message : `监控暂时断开，保留最近快照并自动重试。${sync.message}`}
          </p>}
        </div>
      )}
      <div className="run-buttons">
        <button className="run-button" type="button" onClick={refresh} disabled={busy}>刷新运行</button>
        {loadDemo && <button className="run-button" type="button" onClick={loadDemo} disabled={busy}>载入执行控制演示</button>}
      </div>
      {loadDemo && <p className="flow-muted">演示载入独立的样例项目，回放固定响应。</p>}
    </section>
  )
}
