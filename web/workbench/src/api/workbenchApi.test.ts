import { expect, it, vi } from 'vitest'
import { fixture } from '../mocks/scenario'
import { createHttpWorkbenchApi, createV2ReadApi, withApiDeadline } from './workbenchApi'
import { describeError } from './workbenchFeedback'

it.each([400, 403, 404, 409, 422])('保留 HTTP %i 的 v2 错误字段并提供相应提示', async (status) => {
  const request = vi.fn<typeof fetch>(async () => Response.json({ error: {
    code: 'INVALID_REFERENCE', message: '引用无效', details: { field: 'depends_on', task_id: 'T4' },
  } }, { status }))
  const error = await createV2ReadApi('project', request).getProject().catch((reason: unknown) => reason)
  expect(error).toMatchObject({ status, code: 'INVALID_REFERENCE', details: { field: 'depends_on', task_id: 'T4' } })
  expect(describeError(error)).toMatch(/引用无效.*depends_on.*T4/)
})

it('网络超时取消请求并返回可识别错误', async () => {
  let signal: AbortSignal | undefined
  const result = withApiDeadline((received) => {
    signal = received
    return new Promise<never>(() => {})
  }, 5)
  await expect(result).rejects.toMatchObject({ code: 'TIMEOUT' })
  expect(signal?.aborted).toBe(true)
})

it('合并并发的相同写操作，409 后相同请求使用新 key', async () => {
  let finish!: (response: Response) => void
  const request = vi.fn<typeof fetch>().mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    .mockResolvedValueOnce(Response.json({ ...fixture.project_snapshot, stage: 'intake', latest_plan_ref: null }))
  const actions = createHttpWorkbenchApi('project', request).projectActions!
  const first = actions.createProject('# Demo')
  const duplicate = actions.createProject('# Demo')
  expect(duplicate).toBe(first)
  expect(request).toHaveBeenCalledOnce()
  finish(Response.json({ error: { code: 'VERSION_CONFLICT' } }, { status: 409 }))
  await expect(first).rejects.toMatchObject({ status: 409 })
  await actions.createProject('# Demo')
  const keys = request.mock.calls.map(([, init]) => new Headers(init?.headers).get('Idempotency-Key'))
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).not.toBe(keys[0])
})

it('写操作成功但刷新失败时保留幂等键', async () => {
  const project = { ...fixture.project_snapshot, stage: 'intake', latest_plan_ref: null }
  const request = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(Response.json(fixture.project_snapshot.specification))
    .mockResolvedValueOnce(new Response(null, { status: 404 }))
    .mockResolvedValueOnce(Response.json(fixture.project_snapshot.specification))
    .mockResolvedValueOnce(Response.json(project))
  const actions = createHttpWorkbenchApi(project.id, request).projectActions!
  const ref = { id: 'SPEC-01', version: 1 }
  await expect(actions.confirmSpecification(project.id, ref)).rejects.toMatchObject({ status: 404 })
  await actions.confirmSpecification(project.id, ref)
  const keys = request.mock.calls.filter(([, init]) => init?.method === 'POST')
    .map(([, init]) => new Headers(init?.headers).get('Idempotency-Key'))
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).toBe(keys[0])
})

it('review 端点携带预期修订号、完整版本和幂等键', async () => {
  const { project_snapshot: project, plan_response: plan, review_response: run } = fixture
  const root = `/api/v2/projects/${project.id}`
  const request = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === 'POST') return Response.json(run)
    if (url === root) return Response.json(project)
    if (String(url).includes('/plans/')) return Response.json(plan)
    return Response.json({ items: [], next_after: 0, has_more: false })
  })
  const body = { task_id: 'T2', expected_revision: 8, decision: 'approve' as const,
    expected_versions: { 'ART-T2': 1 }, revisions: {}, note: '已核对' }
  const snapshot = await createHttpWorkbenchApi(project.id, request).runActions!.act(project.id, run.id, { action: 'review', ...body })
  const [url, init] = request.mock.calls[0]
  expect(url).toBe(`${root}/runs/${run.id}/actions/review`)
  expect(JSON.parse(String(init?.body))).toEqual(body)
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy()
  expect(snapshot.run?.tasks.find(({ task_id }) => task_id === 'T4')?.state).toBe('ready')
})

it('读取活动运行所绑定的计划版本，并复用工作台映射', async () => {
  const { project_snapshot, review_response, plan_response, approval_event } = fixture
  const projectPath = '/api/v2/projects/PROJECT-01'
  const runPath = `${projectPath}/runs/RUN-01`
  const fixtures = new Map<string, unknown>([
    [projectPath, { ...project_snapshot, latest_plan_ref: { id: 'PLAN-01', version: 2 } }],
    [runPath, review_response],
    [`${projectPath}/plans/PLAN-01/versions/1`, plan_response],
    [`${runPath}/events?after=0&limit=100`, { items: [{ ...approval_event, seq: 1 }], next_after: 1, has_more: false }],
    [`${projectPath}/plans/PLAN-01/versions/2`, { ...plan_response, plan: { ...plan_response.plan, version: 2 } }],
  ])
  const request = vi.fn<typeof fetch>(async (path) => {
    const data = fixtures.get(String(path))
    return data ? Response.json(data) : new Response(null, { status: 404 })
  })
  const snapshot = await createHttpWorkbenchApi('PROJECT-01', request).getSnapshot()
  expect(snapshot.plan?.ref.version).toBe(1)
  expect(snapshot.latestPlan?.ref.version).toBe(2)
  expect(snapshot.latestPlan?.tasks.find(({ id }) => id === 'T2')?.attempts).toEqual([])
  expect(snapshot.activities[0]?.title).toBe('产物已批准')
  expect(request.mock.calls.map(([path]) => path)).toEqual([...fixtures.keys()])
})

it('PATCH 传入完整任务列表并使用返回的新计划，运行继续读取绑定版本', async () => {
  const { project_snapshot: base, plan_response: initial, review_snapshot: run } = fixture
  const plan = { ...initial.plan, version: 2, max_parallel: 3 }
  const updated = { plan, schedule: null }
  const body = { expected_version: 1, max_parallel: 3, tasks: initial.plan.tasks }
  const root = `/api/v2/projects/${base.id}`
  const calls = new Map<string, unknown>([
    [root, { ...base, latest_plan_ref: { id: plan.id, version: 2 } }],
    [`${root}/plans/${plan.id}/versions/1`, initial],
    [`${root}/runs/${run.id}`, run],
    [`${root}/runs/${run.id}/events?after=0&limit=100`, { items: [], next_after: 0, has_more: false }],
  ])
  const request = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === 'PATCH') return Response.json(updated)
    return calls.has(String(url)) ? Response.json(calls.get(String(url))) : new Response(null, { status: 404 })
  })
  const snapshot = await createHttpWorkbenchApi(base.id, request).savePlan!(base.id, plan.id, body)
  expect(snapshot.latestPlan?.source).toEqual(updated)
  expect(snapshot.plan?.source).toEqual(initial)
  const [url, init] = request.mock.calls[0]
  expect(url).toBe(`${root}/plans/${plan.id}`)
  expect(init?.method).toBe('PATCH')
  expect(new Headers(init?.headers).has('Idempotency-Key')).toBe(true)
  expect(JSON.parse(String(init?.body))).toEqual(body)
  expect(request.mock.calls.some(([path]) => String(path).endsWith('/versions/2'))).toBe(false)
})

it('编码路径参数，读取增量事件并传递 HTTP 错误', async () => {
  const request = vi.fn<typeof fetch>(async () => new Response(null, { status: 404 }))
  const api = createV2ReadApi('p/1', request)
  await expect(api.getEvents('r/1', 9, 20)).rejects.toThrow('HTTP 404')
  expect(request.mock.calls[0][0]).toBe('/api/v2/projects/p%2F1/runs/r%2F1/events?after=9&limit=20')
})

it('使用 v2 契约提交需求、启动赛马、保存并确认规格', async () => {
  const source = fixture.project_snapshot
  const requirement = { ...source.requirement, version: 2 }
  const draft = {
    ...source.specification!, status: 'draft' as const, version: 2,
    requirement_ref: { id: requirement.id, version: 2 }, confirmed_by: null, confirmed_at: null,
  }
  let project = { ...source, requirement, stage: 'intake' as typeof source.stage, latest_plan_ref: null, runs: [], race: null as typeof source.race }
  const path = '/api/v2/projects/PROJECT-01'
  const request = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method !== 'POST') return Response.json(project)
    if (url === `${path}/requirements`) return Response.json(project, { status: 201 })
    if (url === `${path}/race`) {
      project = { ...project, stage: 'race', race: { ...source.race!, requirement_ref: draft.requirement_ref } }
      return Response.json(project, { status: 202 })
    }
    if (url === `${path}/specifications`) {
      project = { ...project, stage: 'specification', specification: draft }
      return Response.json(draft, { status: 201 })
    }
    const confirmed = { ...draft, status: 'confirmed' as const, confirmed_by: 'reviewer', confirmed_at: '2026-11-17T10:00:00Z' }
    project = { ...project, stage: 'planning', specification: confirmed }
    return Response.json(confirmed)
  })
  const actions = createHttpWorkbenchApi(source.id, request).projectActions!
  const requirementBody = { expected_version: 1, markdown: requirement.markdown }
  expect((await actions.saveRequirement(source.id, requirementBody)).projectData.requirement.version).toBe(2)
  expect((await actions.startRace(source.id, 2)).activeStage).toBe('race')
  const body = { expected_version: 1, requirement_version: 2, selection: 'merge' as const,
    markdown: draft.markdown, decisions: draft.decisions, open_items: draft.open_items }
  expect((await actions.saveSpecification(source.id, body)).projectData.specification?.status).toBe('draft')
  expect((await actions.confirmSpecification(source.id, draft)).activeStage).toBe('planning')

  const writes = request.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(writes.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
    [`${path}/requirements`, requirementBody],
    [`${path}/race`, { requirement_version: 2 }],
    [`${path}/specifications`, body],
    [`${path}/specifications/SPEC-01/versions/2/confirm`, {}],
  ])
  expect(writes.every(([, init]) => new Headers(init?.headers).has('Idempotency-Key'))).toBe(true)
})

it('网络重试复用幂等键，版本冲突后重新提交时使用新键', async () => {
  const source = fixture.project_snapshot
  const request = vi.fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError('network'))
    .mockResolvedValueOnce(Response.json({ error: { code: 'VERSION_CONFLICT', message: '版本已变' } }, { status: 409 }))
    .mockResolvedValueOnce(Response.json({ ...source, stage: 'intake', latest_plan_ref: null }))
  const actions = createHttpWorkbenchApi(source.id, request).projectActions!
  const body = { expected_version: 1, markdown: source.requirement.markdown }
  await expect(actions.saveRequirement(source.id, body)).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  await expect(actions.saveRequirement(source.id, body)).rejects.toMatchObject({ code: 'VERSION_CONFLICT', status: 409 })
  await actions.saveRequirement(source.id, { ...body, expected_version: 2 })
  const keys = request.mock.calls.map(([, init]) => new Headers(init?.headers).get('Idempotency-Key'))
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).toBe(keys[0])
  expect(keys[2]).not.toBe(keys[0])
})

it('运行启动按契约提交计划和模式，运行操作发送修订号并复用失败请求的幂等键', async () => {
  const project = fixture.project_snapshot
  const run = fixture.review_snapshot
  const paused = { ...run, dispatch: 'paused' as const, revision: run.revision + 1 }
  const path = `/api/v2/projects/${project.id}`
  let actionAttempts = 0
  const request = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === 'POST') {
      if (url === `${path}/runs`) return Response.json(run, { status: 201 })
      if (++actionAttempts === 1) throw new TypeError('network')
      return Response.json(paused)
    }
    if (url === path) return Response.json(project)
    if (String(url).includes('/plans/')) return Response.json(fixture.plan_response)
    if (String(url).includes('/events?')) return Response.json({ items: [], next_after: 0, has_more: false })
    return new Response(null, { status: 404 })
  })
  const actions = createHttpWorkbenchApi(project.id, request).runActions!
  const startBody = { plan_ref: run.plan_ref, mode: 'manual' as const }
  expect((await actions.start(project.id, startBody)).run?.mode).toBe('manual')
  const command = { action: 'pause' as const, expected_revision: run.revision }
  await expect(actions.act(project.id, run.id, command)).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  expect((await actions.act(project.id, run.id, command)).run?.dispatch).toBe('paused')
  const writes = request.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(writes.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
    [`${path}/runs`, startBody],
    [`${path}/runs/${run.id}/actions/pause`, { expected_revision: run.revision }],
    [`${path}/runs/${run.id}/actions/pause`, { expected_revision: run.revision }],
  ])
  const keys = writes.map(([, init]) => new Headers(init?.headers).get('Idempotency-Key'))
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).toBeTruthy()
  expect(keys[2]).toBe(keys[1])
})

it('服务端已提交但响应丢失时显式重试，以相同键重放而不重复写入', async () => {
  const project = { ...fixture.project_snapshot, stage: 'intake', latest_plan_ref: null }
  const committed = new Map<string, typeof project>()
  const request = vi.fn<typeof fetch>(async (_url, init) => {
    const key = new Headers(init?.headers).get('Idempotency-Key')!
    if (committed.has(key)) return Response.json(committed.get(key), { status: 201 })
    committed.set(key, project)
    throw new TypeError('response lost after commit')
  })
  const actions = createHttpWorkbenchApi(project.id, request).projectActions!
  await expect(actions.createProject('# 测试')).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  expect(request).toHaveBeenCalledOnce()
  await expect(actions.createProject('# 测试')).resolves.toMatchObject({ projectData: { id: project.id } })
  expect(request).toHaveBeenCalledTimes(2)
  expect(committed.size).toBe(1)
})
