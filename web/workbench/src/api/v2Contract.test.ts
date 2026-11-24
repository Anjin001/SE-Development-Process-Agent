import { expect, it, vi } from 'vitest'
import { fixture } from '../mocks/scenario'
import { resolveWorkbench } from './workbenchMode'
import { createHttpWorkbenchApi, createV2ReadApi } from './workbenchApi'

const { project_snapshot: project, plan_response: plan, review_response: run, approval_event: event } = fixture
const json = (value: unknown) => vi.fn<typeof fetch>(async () => Response.json(value))
const invalid = { code: 'INVALID_RESPONSE', status: 0 }

it.each([
  { ...project, id: 'OTHER' },
  { ...project, specification: undefined },
  { ...project, requirement: { ...project.requirement, version: '1' } },
  { ...project, runs: [null] },
])('拒绝错误项目或缺少字段的项目快照', async (value) => {
  await expect(createV2ReadApi(project.id, json(value)).getProject()).rejects.toMatchObject(invalid)
})

it('项目创建成功 HTTP 状态也不能掩盖不完整快照', async () => {
  const request = vi.fn<typeof fetch>(async () => Response.json({ id: project.id }, { status: 201 }))
  await expect(createHttpWorkbenchApi(project.id, request).projectActions!.createProject('# 需求'))
    .rejects.toMatchObject(invalid)
  expect(request).toHaveBeenCalledOnce()
  expect(request.mock.calls[0][0]).toBe('/api/v2/projects')
})

it.each([
  { ...plan, plan: { ...plan.plan, version: 2 } },
  { ...plan, plan: { ...plan.plan, tasks: [{ ...plan.plan.tasks[0], estimate_seconds: '300' }] } },
  { ...plan, schedule: [{ task_id: 'UNKNOWN', early_start_seconds: 0, early_finish_seconds: 1, critical: true }] },
])('拒绝版本或排期不一致的计划响应', async (value) => {
  await expect(createV2ReadApi(project.id, json(value)).getPlan(plan.plan)).rejects.toMatchObject(invalid)
})

it.each([
  { ...run, tasks: Object.fromEntries(run.tasks.map((task) => [task.task_id, task])) },
  { ...run, revision: '9' },
  { ...run, tasks: run.tasks.map((task) => ({ ...task, review_records: undefined })) },
  { ...run, tasks: run.tasks.map((task) => ({ ...task, artifacts: task.artifacts.map((item) =>
    ({ ...item, changed_paths: undefined })) })) },
  { ...run, tasks: run.tasks.map((task) => ({ ...task, artifacts: task.artifacts.map((item) =>
    ({ ...item, based_on_version: item.version })) })) },
])('拒绝非契约的运行、审核及产物版本数据', async (value) => {
  await expect(createV2ReadApi(project.id, json(value)).getRun(run.id)).rejects.toMatchObject(invalid)
})

it('读取审核修订版本和已放行的下游状态', async () => {
  const result = await createV2ReadApi(project.id, json(run)).getRun(run.id)
  const task = result.tasks.find(({ task_id }) => task_id === 'T2')!
  expect(task.artifacts.map(({ version, based_on_version }) => [version, based_on_version])).toEqual([[1, null], [2, 1]])
  expect(task.review_records[0].approved_versions).toEqual({ 'ART-T2': 2 })
  expect(result.tasks.find(({ task_id }) => task_id === 'T4')?.state).toBe('ready')
})

it('单步响应按契约读取裸 Run，拒绝后端当前包装回包', async () => {
  const request = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === 'POST') return Response.json({ dispatched: [], run })
    if (String(url).endsWith('/events?after=0&limit=100')) {
      return Response.json({ items: [], next_after: 0, has_more: false })
    }
    return Response.json(String(url).includes('/plans/') ? plan : project)
  })
  const actions = createHttpWorkbenchApi(project.id, request).runActions!
  const command = { action: 'step' as const, expected_revision: run.revision - 1 }
  await expect(actions.act(project.id, run.id, command)).rejects.toMatchObject(invalid)
  expect(request).toHaveBeenCalledOnce()
  expect(request.mock.calls[0][0]).toBe(`/api/v2/projects/${project.id}/runs/${run.id}/actions/step`)
  expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual({ expected_revision: command.expected_revision })
  request.mockResolvedValueOnce(Response.json(run))
  await expect(actions.act(project.id, run.id, command)).resolves.toMatchObject({ run: { id: run.id } })
})

it.each([
  { items: [{ ...event, payload: [] }], next_after: 10, has_more: false },
  { items: [], next_after: 9, has_more: true },
  { items: [{ ...event, run_id: 'OTHER' }], next_after: 10, has_more: false },
])('拒绝错误的事件页或跨运行事件', async (value) => {
  await expect(createV2ReadApi(project.id, json(value)).getEvents(run.id, 9)).rejects.toMatchObject(invalid)
})

it('真实模式遇到非法 JSON 不进入 Mock', async () => {
  const request = vi.fn<typeof fetch>(async () => new Response('<html>代理错误</html>'))
  await expect(resolveWorkbench({ VITE_WORKBENCH_MODE: 'v2', VITE_WORKBENCH_PROJECT_ID: project.id }, request))
    .rejects.toMatchObject(invalid)
  expect(request).toHaveBeenCalledOnce()
})
