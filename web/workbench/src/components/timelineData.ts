import type { Attempt } from '../types/v2'
import type { WorkbenchPlan } from '../types/workbench'

function timestamp(value: string | null) {
  const time = value === null ? NaN : Date.parse(value)
  return Number.isFinite(time) ? time : null
}

function actualTiming(attempt: Attempt, origin: number | null) {
  const start = timestamp(attempt.started_at)
  const end = timestamp(attempt.ended_at)
  const duration = start !== null && end !== null && end >= start ? (end - start) / 1000 : null
  const offset = start !== null && origin !== null && start >= origin ? (start - origin) / 1000 : null
  return { attempt, start: offset, end: offset !== null && duration !== null ? offset + duration : null, duration }
}

export function buildTimeline(plan: WorkbenchPlan, startedAt: string | null) {
  const origin = timestamp(startedAt)
  const schedule = new Map(plan.source.schedule?.map((entry) => [entry.task_id, entry]))
  const rows = plan.tasks.map((task) => ({
    task,
    planned: schedule.get(task.id),
    actual: task.attempts.map((attempt) => actualTiming(attempt, origin)),
  }))
  let latestSecond = plan.plannedSeconds ?? 0
  let hasActualPosition = false
  let hasActualDuration = false
  for (const { actual } of rows) {
    for (const timing of actual) {
      if (timing.start !== null) {
        hasActualPosition = true
        latestSecond = Math.max(latestSecond, timing.end ?? timing.start)
      }
      if (timing.duration !== null) hasActualDuration = true
    }
  }

  const minutes = Math.max(1, Math.ceil(latestSecond / 60))
  const roughStep = Math.max(1, minutes / 8)
  const unit = 10 ** Math.floor(Math.log10(roughStep))
  const step = ([1, 2, 5, 10].find((factor) => unit * factor >= roughStep) ?? 10) * unit
  const intervals = Math.ceil(minutes / step)
  const ticks = Array.from({ length: intervals + 1 }, (_, index) => index * step)
  return { rows, ticks, totalSeconds: intervals * step * 60, hasActualPosition, hasActualDuration }
}
