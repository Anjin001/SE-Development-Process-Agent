import type { PlanResponse, ProjectSnapshot, RunEventsPage, VersionRef } from '../types/v2'
import { WorkbenchApiError } from './workbenchApi'

function invalid(message: string): never {
  throw new WorkbenchApiError(message, 'INVALID_RESPONSE', 0)
}

const validRef = (ref: VersionRef | null | undefined) =>
  typeof ref?.id === 'string' && Number.isSafeInteger(ref.version) && ref.version > 0

export function mapV2Project(value: ProjectSnapshot, id: string) {
  if (value?.id !== id || !['intake', 'race', 'specification', 'planning', 'execution', 'completed'].includes(value.stage)
      || !validRef(value.requirement) || typeof value.requirement.markdown !== 'string'
      || !Array.isArray(value.requirement.items) || !Array.isArray(value.drafts)
      || !Array.isArray(value.evaluations) || !Array.isArray(value.runs) || !Array.isArray(value.findings)
      || (value.latest_plan_ref !== null && !validRef(value.latest_plan_ref))
      || (value.race !== null && !Array.isArray(value.race?.groups))) {
    invalid('项目响应不符合标准 v2 快照结构。')
  }
  const groups = value.race?.groups.map((group) => group?.group_id)
  if (groups?.some((group) => group !== 'A' && group !== 'B')) invalid('该项目包含额外赛马小组，当前工作台仅支持 A/B。')
  return value
}

export function mapV2Plan(value: PlanResponse, id: string, version?: number) {
  if (!validRef(value?.plan) || value.plan.id !== id || (version !== undefined && value.plan.version !== version)
      || !validRef(value.plan.spec_ref) || !Array.isArray(value.plan.tasks)
      || (value.schedule !== null && !Array.isArray(value.schedule))) {
    invalid('计划响应不符合 v2 版本或排期结构。')
  }
  return value
}

export function mapV2Events(value: RunEventsPage, runId: string, after: number) {
  if (!Array.isArray(value?.items) || typeof value.has_more !== 'boolean'
      || value.items.some((event, index) => event?.run_id !== runId || event.seq !== after + index + 1)
      || value.next_after !== after + value.items.length || (value.has_more && !value.items.length)) {
    invalid('运行事件的游标或序号不连续。')
  }
  return value
}
