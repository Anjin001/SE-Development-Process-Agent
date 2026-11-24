import { expect, it, vi } from 'vitest'
import { fixture } from '../mocks/scenario'
import { createHttpWorkbenchApi, createV2ReadApi } from './workbenchApi'
import { resolveWorkbench } from './workbenchMode'
import { validateV2Bindings } from './v2Mapping'

const { project_snapshot: project, plan_response: plan, review_response: run } = fixture
const invalid = { code: 'INVALID_RESPONSE' }

it('拒绝运行所属项目与请求不同，即使运行 ID 相同', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ ...run, project_id: 'OTHER' }))
  await expect(createV2ReadApi(project.id, fetcher).getRun(run.id)).rejects.toMatchObject(invalid)
})

it('拒绝上一版需求的赛马状态', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({
    ...project, requirement: { ...project.requirement, version: project.requirement.version + 1 },
  }))
  await expect(createV2ReadApi(project.id, fetcher).getProject()).rejects.toMatchObject(invalid)
})

it('拒绝计划引用另一规格版本或在当前需求中不存在的条目', () => {
  const wrongVersion = { ...plan, plan: { ...plan.plan, spec_ref: { ...plan.plan.spec_ref, version: 99 } } }
  expect(() => validateV2Bindings(project, wrongVersion, wrongVersion, run)).toThrow(/规格版本/)
  const wrongItem = { ...plan, plan: { ...plan.plan, tasks: plan.plan.tasks.map((task) =>
    ({ ...task, requirement_ids: ['UNKNOWN'] })) } }
  expect(() => validateV2Bindings(project, wrongItem, wrongItem, run)).toThrow(/不存在的条目/)
})

it('不同版本的最新计划与历史运行各用各的引用', () => {
  const latest = { ...plan, plan: { ...plan.plan, version: 2 } }
  const changed = { ...project, latest_plan_ref: { id: latest.plan.id, version: 2 } }
  expect(() => validateV2Bindings(changed, plan, latest, run)).not.toThrow()
  expect(() => validateV2Bindings(changed, latest, latest, run)).toThrow(/绑定计划版本/)
})

it('拒绝运行任务及产物关联到其他计划输出', () => {
  const wrongOutput = { ...run, tasks: run.tasks.map((task) => ({ ...task, artifacts: task.artifacts.map((item) =>
    ({ ...item, output_id: 'OTHER' })) })) }
  expect(() => validateV2Bindings(project, plan, plan, wrongOutput)).toThrow(/计划的任务及输出/)
})

it('拒绝审核记录引用不存在的产物版本', async () => {
  const wrongReview = { ...run, tasks: run.tasks.map((task) => ({ ...task, review_records: task.review_records.map((item) =>
    ({ ...item, approved_versions: { 'ART-T2': 99 } })) })) }
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(wrongReview))
  await expect(createV2ReadApi(project.id, fetcher).getRun(run.id)).rejects.toMatchObject(invalid)
})

it('拒绝动作返回项目摘要中未登记的运行版本', async () => {
  const fetcher = vi.fn<typeof fetch>(async (_path, init) => Response.json(init?.method === 'POST'
    ? { ...run, plan_ref: { ...run.plan_ref, version: 99 } } : project))
  await expect(createHttpWorkbenchApi(project.id, fetcher).runActions!.act(project.id, run.id,
    { action: 'pause', expected_revision: run.revision })).rejects.toMatchObject(invalid)
})

it('局部真实连接不读取其他项目，也不附带 Mock 数据', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(project))
  const { api } = await resolveWorkbench({ VITE_WORKBENCH_MODE: 'v2', VITE_WORKBENCH_PROJECT_ID: project.id }, fetcher)
  await expect(api.getSnapshot('OTHER')).rejects.toMatchObject({ status: 403 })
  expect(fetcher).toHaveBeenCalledOnce()
  expect(api.loadRunDemo).toBeUndefined()
  expect(api.runActions).toBeUndefined()
})

it('重新进入 Mock 时使用独立项目仓储', async () => {
  const first = await resolveWorkbench({})
  const created = await first.api.projectActions!.createProject('# 第一份')
  const second = await resolveWorkbench({})
  await expect(second.api.getSnapshot(created.projectData.id)).rejects.toMatchObject({ status: 404 })
})
