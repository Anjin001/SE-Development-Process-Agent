import { expect, it, vi } from 'vitest'
import { fixture } from '../mocks/scenario'
import { resolveWorkbench } from './workbenchMode'
import { createPartialV2Api } from './partialV2Api'

const { project_snapshot: project, plan_response: plan, approval_event: event } = fixture
const root = `http://127.0.0.1:8008/api/v2/projects/${project.id}`
const env = { VITE_WORKBENCH_MODE: 'v2', VITE_WORKBENCH_API_BASE: 'http://127.0.0.1:8008/', VITE_WORKBENCH_PROJECT_ID: project.id }

it('默认 Mock 不请求网络，真实项目 404 明确失败并提示显式切换 Mock', async () => {
  const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 }))
  expect((await resolveWorkbench({}, request)).api.loadRunDemo).toBeDefined()
  expect(request).not.toHaveBeenCalled()
  await expect(resolveWorkbench(env, request)).rejects.toMatchObject({ status: 404, message: expect.stringContaining('mock') })
  expect(request.mock.calls[0][0]).toBe(root)
  request.mockResolvedValue(new Response(null, { status: 503 }))
  await expect(resolveWorkbench(env, request)).rejects.toMatchObject({ status: 503 })
})

it('真实模式读取并更新授权计划，不调用尚未统一的运行和审核接口', async () => {
  const request = vi.fn<typeof fetch>(async (url, init) => {
    if (url === root) return Response.json(project)
    if (url === `${root}/plans/PLAN-01/versions/1`) return Response.json(plan)
    if (url === `${root}/plans/PLAN-01` && init?.method === 'PATCH') {
      return Response.json({ ...plan, plan: { ...plan.plan, version: 2, max_parallel: 3 } })
    }
    throw new Error(`Unexpected request ${url}`)
  })
  const readOnly = await resolveWorkbench(env, request)
  expect(readOnly.api.savePlan).toBeUndefined()
  const result = await resolveWorkbench({ ...env, VITE_WORKBENCH_PLAN_ID: 'PLAN-01' }, request)
  expect((await result.api.getSnapshot()).latestPlan?.ref.version).toBe(1)
  expect(result.api.runActions).toBeUndefined()
  expect(result.api.projectActions).toBeUndefined()
  await expect(result.api.savePlan!(project.id, 'OTHER', { expected_version: 1 })).rejects.toMatchObject({ status: 403 })
  const saved = await result.api.savePlan!(project.id, 'PLAN-01', { expected_version: 1, max_parallel: 3 })
  expect(saved.latestPlan?.ref.version).toBe(2)
  const [, init] = request.mock.calls.at(-1)!
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy()
})

it('计划 404 显示原错误，绝不切换到 Mock', async () => {
  const request = vi.fn<typeof fetch>()
    .mockImplementation(async (path) => path === root ? Response.json(project) : new Response(null, { status: 404 }))
  const result = await resolveWorkbench(env, request)
  await expect(result.api.getSnapshot()).rejects.toMatchObject({ status: 404 })
})

it('独立事件接口保留游标，拒绝断序回包', async () => {
  const request = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(Response.json({ items: [event], next_after: 10, has_more: false }))
    .mockResolvedValueOnce(Response.json({ items: [event], next_after: 10, has_more: false }))
  const api = createPartialV2Api(project.id, request)
  expect(await api.getEvents(event.run_id, 9, 1)).toMatchObject({ next_after: 10 })
  expect(request.mock.calls[0][0]).toContain('/events?after=9&limit=1')
  await expect(api.getEvents(event.run_id, 8)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
})
