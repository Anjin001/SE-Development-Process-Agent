import { sameVersion } from '../api/projectVersions'
import type { Plan, UpdatePlanRequest, VersionRef } from '../types/v2'
import { buildPlanUpdate, estimateInMinutes } from './planDraft'
import type { PlanDraft, TaskEdits } from './planDraft'
import { formatVersion } from './presentation'

export interface PlanEditor {
  draft: PlanDraft | null
  onDraftChange: (draft: PlanDraft | null) => void
  onSave?: (id: string, body: UpdatePlanRequest) => Promise<boolean>
  busy: boolean
}

export function TaskConfiguration({ plan, runPlan, selectedId, editor }: {
  plan: Plan | null
  runPlan: VersionRef | null
  selectedId?: string
  editor: PlanEditor
}) {
  const { draft, onDraftChange, onSave, busy } = editor
  const base = draft?.base ?? plan
  const stale = draft !== null && !sameVersion(draft.base, plan)
  const canRebase = stale && plan && draft.base.id === plan.id &&
    [...draft.tasks.keys()].every((id) => plan.tasks.some((task) => task.id === id))

  if (!base) {
    return <section className="flow-card"><h3>任务配置</h3><p className="flow-muted">暂无可配置的计划。</p></section>
  }

  const current = draft ?? { base, tasks: new Map<string, TaskEdits>() }
  const task = base.tasks.find((item) => item.id === selectedId) ?? base.tasks[0]
  const edits = task ? current.tasks.get(task.id) : undefined
  const dependencies = edits?.dependsOn ?? task?.depends_on ?? []
  const missingDependencies = dependencies.filter((id) => !base.tasks.some((item) => item.id === id))
  const { body, errors } = buildPlanUpdate(current)

  function editTask(change: TaskEdits) {
    if (!task) return
    const tasks = new Map(current.tasks)
    tasks.set(task.id, { ...edits, ...change })
    onDraftChange({ ...current, tasks })
  }

  function toggleDependency(id: string, checked: boolean) {
    editTask({ dependsOn: checked ? [...dependencies, id] : dependencies.filter((value) => value !== id) })
  }

  async function save() {
    if (!body || !onSave || stale || busy) return
    if (await onSave(current.base.id, body)) onDraftChange(null)
  }

  return (
    <form className="flow-card plan-editor" onSubmit={(event) => { event.preventDefault(); void save() }} noValidate>
      <div className="flow-heading">
        <h3>任务配置</h3>
        {plan && <span className="chip chip-blue">最新计划 {formatVersion(plan)}</span>}
      </div>
      {runPlan && <p className="flow-muted">现有运行固定使用 {formatVersion(runPlan)}。保存配置会生成新版本。</p>}
      {stale && (
        <div className="flow-alert">
          <p>草稿基于 {formatVersion(base)}，编辑内容仍保留。核对最新版后再保存。</p>
          {canRebase && (
            <button type="button" disabled={busy} onClick={() => onDraftChange({ ...draft, base: plan })}>
              将本地修改应用到最新版本
            </button>
          )}
        </div>
      )}
      <fieldset disabled={busy || !onSave || stale}>
        <div className="plan-fields">
          <label>
            计划并发上限
            <input type="number" min="1" step="1" value={current.maxParallel ?? String(base.max_parallel)}
              onChange={(event) => onDraftChange({ ...current, maxParallel: event.target.value })} />
          </label>
          {task && (
            <>
              <label>
                执行角色
                <input value={edits?.role ?? task.role} onChange={(event) => editTask({ role: event.target.value })} />
              </label>
              <label>
                估时（分钟）
                <input type="number" min="0" step="any" value={edits?.estimateMinutes ?? estimateInMinutes(task.estimate_seconds)}
                  onChange={(event) => editTask({ estimateMinutes: event.target.value })} aria-describedby="estimate-hint" />
              </label>
            </>
          )}
        </div>
        {task && (
          <>
            <p className="flow-muted" id="estimate-hint">按最接近的整秒保存，留空表示未估时。</p>
            <p className="plan-edit-task"><strong>正在编辑 {task.id} · {task.title}</strong></p>
            <fieldset className="plan-dependencies">
              <legend>直接前置任务</legend>
              {base.tasks.length <= 1 && missingDependencies.length === 0 && <p className="flow-muted">暂无可选的前置任务。</p>}
              {base.tasks.filter(({ id }) => id !== task.id).map(({ id, title }) => (
                <label key={id}>
                  <input type="checkbox" checked={dependencies.includes(id)} onChange={(event) => toggleDependency(id, event.target.checked)} />
                  {id} · {title}
                </label>
              ))}
              {missingDependencies.map((id) => (
                <label key={id}>
                  <input type="checkbox" checked onChange={() => toggleDependency(id, false)} />
                  {id}（最新计划中已移除，取消勾选以清除引用）
                </label>
              ))}
            </fieldset>
            <label className="plan-review">
              <input type="checkbox" checked={edits?.reviewRequired ?? task.review_required}
                onChange={(event) => editTask({ reviewRequired: event.target.checked })} />
              需要人工审核
            </label>
          </>
        )}
      </fieldset>
      {errors.length > 0 && (
        <ul className="flow-alert plan-errors" role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul>
      )}
      <div className="flow-actions">
        <button type="submit" className="flow-primary" disabled={!onSave || busy || stale || !body}>保存计划</button>
        <button type="button" disabled={busy || !draft} onClick={() => onDraftChange(null)}>取消修改</button>
      </div>
    </form>
  )
}
