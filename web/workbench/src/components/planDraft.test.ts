import { expect, it } from 'vitest'
import { fixture } from '../mocks/scenario'
import { buildPlanUpdate, estimateInMinutes } from './planDraft'

const base = fixture.plan_response.plan

it('只修改并发时省略 tasks，修改任务时保留完整的契约字段', () => {
  expect(buildPlanUpdate({ base, maxParallel: '3', tasks: new Map() }).body).toEqual({ expected_version: 1, max_parallel: 3 })
  const { body, errors } = buildPlanUpdate({
    base, tasks: new Map([['T2', { role: 'reviewer', estimateMinutes: '1.25', dependsOn: ['T1', 'T3'], reviewRequired: false }]]),
  })
  expect(errors).toEqual([])
  expect(body?.tasks).toHaveLength(base.tasks.length)
  expect(body?.tasks?.[1]).toEqual({ ...base.tasks[1], role: 'reviewer', estimate_seconds: 75, depends_on: ['T1', 'T3'], review_required: false })
  expect(body?.tasks?.[0]).toEqual(base.tasks[0])
})

it('空估时发送 null，分钟显示正确往返已有秒数', () => {
  const { body } = buildPlanUpdate({ base, tasks: new Map([['T2', { estimateMinutes: '' }]]) })
  expect(body?.tasks?.[1].estimate_seconds).toBeNull()
  const { body: short } = buildPlanUpdate({ base, tasks: new Map([['T2', { estimateMinutes: estimateInMinutes(1) }]]) })
  expect(short?.tasks?.[1].estimate_seconds).toBe(1)
})

it('阻止非法的表单值和自引用，成环校验交给 API', () => {
  const result = buildPlanUpdate({
    base, maxParallel: '1.5', tasks: new Map([['T2', { estimateMinutes: '-1', role: ' ', dependsOn: ['T2'] }]]),
  })
  expect(result.body).toBeNull()
  expect(result.errors).toHaveLength(4)
  expect(buildPlanUpdate({ base, tasks: new Map([['T1', { dependsOn: ['T4'] }]]) }).body).not.toBeNull()
})
