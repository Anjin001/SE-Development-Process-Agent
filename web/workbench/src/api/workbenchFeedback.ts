import type { WorkbenchSnapshot } from '../types/workbench'
import { WorkbenchApiError } from './workbenchApi'

const statusText: Record<number, string> = {
  400: '请求参数有误。', 403: '没有执行此操作的权限。', 404: '资源不存在或已移除。',
  409: '当前状态与请求冲突。', 422: '数据未通过校验。',
}

export function describeError(error: unknown) {
  if (!(error instanceof WorkbenchApiError)) return '操作未完成，请重试。'
  const details = Object.entries(error.details).map(([key, value]) => {
    if (key === 'cycle' && Array.isArray(value)) return value.join(' → ')
    return `${key} = ${typeof value === 'string' ? value : JSON.stringify(value)}`
  })
  return [statusText[error.status], error.message, error.code === 'HTTP_ERROR' ? '' : error.code, ...details].filter(Boolean).join(' ')
}

export function describeConflict(before: WorkbenchSnapshot, after: WorkbenchSnapshot) {
  const versions = (snapshot: WorkbenchSnapshot) => [
    ['需求', snapshot.projectData.requirement.version],
    ['规格', snapshot.specificationRef?.version],
    ['计划', snapshot.latestPlan?.ref.version],
    ['运行修订号', snapshot.run?.revision],
  ] as const
  const previous = new Map(versions(before))
  const summary = versions(after).map(([label, value]) => {
    const old = previous.get(label)
    return `${label} ${old !== value ? `${old ?? '无'} → ` : ''}${value ?? '无'}`
  })
  if (before.latestPlan?.concurrency !== after.latestPlan?.concurrency) {
    summary.push(`并发上限 ${before.latestPlan?.concurrency ?? '无'} → ${after.latestPlan?.concurrency ?? '无'}`)
  }
  const oldTasks = new Map(before.latestPlan?.source.plan.tasks.map((task) => [task.id, task]))
  const newTasks = new Map(after.latestPlan?.source.plan.tasks.map((task) => [task.id, task]))
  const changed = [...new Set([...oldTasks.keys(), ...newTasks.keys()])]
    .filter((id) => JSON.stringify(oldTasks.get(id)) !== JSON.stringify(newTasks.get(id)))
  if (changed.length) summary.push(`变更任务：${changed.join('、')}`)
  const oldArtifacts = new Map(before.run?.tasks.flatMap((task) => task.artifacts.map((item) => [`${item.id}@v${item.version}`, item.status] as const)))
  const artifacts = after.run?.tasks.flatMap((task) => task.artifacts)
    .filter((item) => oldArtifacts.get(`${item.id}@v${item.version}`) !== item.status)
    .map((item) => `${item.id}@v${item.version} (${item.status})`)
  if (artifacts?.length) summary.push(`产物变更：${artifacts.join('、')}`)
  return summary.join(' · ')
}
