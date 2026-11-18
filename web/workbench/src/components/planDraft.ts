import type { Plan, Task, UpdatePlanRequest } from '../types/v2'

export interface TaskEdits {
  role?: string
  estimateMinutes?: string
  dependsOn?: readonly string[]
  reviewRequired?: boolean
}

export interface PlanDraft {
  base: Plan
  maxParallel?: string
  tasks: ReadonlyMap<string, TaskEdits>
}

export function estimateInMinutes(seconds: number | null) {
  return seconds === null ? '' : String(Number((seconds / 60).toFixed(4)))
}

function sameDependencies(a: readonly string[], b: readonly string[]) {
  return a.length === b.length && a.every((id) => b.includes(id))
}

// Form validation stops at field constraints. The API validates the DAG.
export function buildPlanUpdate(draft: PlanDraft): { body: UpdatePlanRequest | null; errors: string[] } {
  const { base } = draft
  const errors: string[] = []
  const concurrency = draft.maxParallel === undefined ? base.max_parallel : Number(draft.maxParallel)
  if (!Number.isSafeInteger(concurrency) || concurrency <= 0) errors.push('计划并发上限必须为正整数。')

  const taskIds = new Set(base.tasks.map(({ id }) => id))
  let changedTasks = false
  const tasks = base.tasks.map((task): Task => {
    const edits = draft.tasks.get(task.id)
    if (!edits) return task
    const role = edits.role === undefined ? task.role : edits.role.trim()
    if (!role) errors.push(`${task.id}：执行角色不能为空。`)
    let seconds = task.estimate_seconds
    if (edits.estimateMinutes !== undefined) {
      seconds = edits.estimateMinutes.trim() === '' ? null : Math.round(Number(edits.estimateMinutes) * 60)
      if (seconds !== null && (!Number.isSafeInteger(seconds) || seconds <= 0)) {
        errors.push(`${task.id}：估时须换算成正整数秒，或留空。`)
      }
    }
    const dependencies = edits.dependsOn ?? task.depends_on
    if (dependencies.includes(task.id) || dependencies.some((id) => !taskIds.has(id))) {
      errors.push(`${task.id}：前置任务必须是计划中存在的其他任务。`)
    }
    const review = edits.reviewRequired ?? task.review_required
    if (role === task.role && seconds === task.estimate_seconds && review === task.review_required &&
        sameDependencies(dependencies, task.depends_on)) return task
    changedTasks = true
    return { ...task, role, estimate_seconds: seconds, depends_on: dependencies, review_required: review }
  })
  if (errors.length || (!changedTasks && concurrency === base.max_parallel)) return { body: null, errors }
  const body: UpdatePlanRequest = { expected_version: base.version }
  if (concurrency !== base.max_parallel) body.max_parallel = concurrency
  if (changedTasks) body.tasks = tasks
  return { body, errors }
}
