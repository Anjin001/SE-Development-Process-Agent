// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { createMockWorkbenchApi } from './api/mockWorkbenchApi'
import { mapWorkbenchSnapshot } from './api/mapWorkbenchSnapshot'
import type { WorkbenchApi } from './api/workbenchApi'
import type { RunEventsPage } from './types/v2'
import { fixture } from './mocks/scenario'

beforeEach(() => {
  sessionStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const { project_snapshot: sample, plan_response: plan, review_snapshot: run, approval_event: event } = fixture
const snapshot = (id: string, sequence = run.latest_event_seq, dispatch = run.dispatch) =>
  mapWorkbenchSnapshot({ ...sample, id, requirement: { ...sample.requirement, markdown: '# ' + id } },
    plan, { ...run, latest_event_seq: sequence, dispatch }, [])

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((finish) => { resolve = finish })
  return { promise, resolve }
}

it('切换项目后忽略迟到的初始快照', async () => {
  const old = deferred<ReturnType<typeof snapshot>>()
  const api = { getSnapshot: vi.fn((id) => id === 'A' ? old.promise : Promise.resolve(snapshot('B'))) } satisfies WorkbenchApi
  const { rerender } = render(<App api={api} projectId="A" />)
  rerender(<App api={api} projectId="B" />)
  await screen.findByRole('heading', { name: 'B' })
  await act(async () => old.resolve(snapshot('A')))
  expect(screen.queryByRole('heading', { name: 'A' })).toBeNull()
})

it('相同运行 ID 的两个项目分别初始化游标，并忽略切换后的旧事件', async () => {
  const old = deferred<RunEventsPage>()
  const getEvents = vi.fn(async (project: string, _run: string, after: number) =>
    project === 'A' ? old.promise : { items: [], has_more: false, next_after: after })
  const getRun = vi.fn(async () => fixture.review_response)
  const api: WorkbenchApi = {
    getSnapshot: async (id) => snapshot(id!, id === 'B' ? 3 : 9),
    runReads: { getEvents, getRun },
  }
  const { rerender } = render(<App api={api} projectId="A" monitorInterval={20} />)
  await waitFor(() => expect(getEvents).toHaveBeenCalledWith('A', run.id, 9))
  rerender(<App api={api} projectId="B" monitorInterval={20} />)
  await waitFor(() => expect(getEvents).toHaveBeenCalledWith('B', run.id, 3))
  await act(async () => old.resolve({ items: [event], has_more: false, next_after: 10 }))
  expect(screen.queryByText('ART-T2@v2')).toBeNull()
  expect(getRun).not.toHaveBeenCalled()
  expect(getEvents.mock.calls.filter(([project]) => project === 'A')).toHaveLength(1)
})

it('旧项目的写回包不能修改当前项目的运行状态', async () => {
  const old = deferred<ReturnType<typeof snapshot>>()
  const api: WorkbenchApi = {
    getSnapshot: async (id) => snapshot(id!),
    runActions: { start: async (id) => snapshot(id), act: vi.fn(() => old.promise) },
  }
  const { rerender } = render(<App api={api} projectId="A" />)
  fireEvent.click(await screen.findByRole('button', { name: '暂停派发' }))
  rerender(<App api={api} projectId="B" />)
  await screen.findByRole('button', { name: '暂停派发' })
  await act(async () => old.resolve(snapshot('A', 10, 'paused')))
  expect(screen.queryByRole('button', { name: '继续派发' })).toBeNull()
  expect(api.runActions!.act).toHaveBeenCalledWith('A', run.id, expect.anything())
})

it('拒绝同会话中意外返回另一项目的写回包', async () => {
  const api: WorkbenchApi = {
    getSnapshot: async () => snapshot('A'),
    runActions: { start: async () => snapshot('A'), act: async () => snapshot('B', 10, 'paused') },
  }
  render(<App api={api} projectId="A" />)
  fireEvent.click(await screen.findByRole('button', { name: '暂停派发' }))
  expect(await screen.findByText(/响应与当前项目不一致/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: '继续派发' })).toBeNull()
})

it('需求草稿切回项目后恢复，切换数据源后不复用', async () => {
  const api = createMockWorkbenchApi()
  const a = (await api.projectActions!.createProject('# 项目 A')).projectData.id
  const b = (await api.projectActions!.createProject('# 项目 B')).projectData.id
  const editor = () => screen.findByRole('textbox', { name: '编辑需求原文' })
  const { rerender } = render(<App api={api} projectId={a} />)
  fireEvent.change(await editor(), { target: { value: '# A 未提交' } })
  rerender(<App api={api} projectId={b} />)
  expect((await editor() as HTMLTextAreaElement).value).toBe('# 项目 B')
  fireEvent.change(await editor(), { target: { value: '# B 未提交' } })
  rerender(<App api={api} projectId={a} />)
  expect((await editor() as HTMLTextAreaElement).value).toBe('# A 未提交')
  const real: WorkbenchApi = { getSnapshot: async () => api.getSnapshot(a) }
  rerender(<App api={real} projectId={a} />)
  expect((await editor() as HTMLTextAreaElement).value).toBe('# 项目 A')
})

it('审核草稿按项目、运行和任务保留，同名产物不会串用', async () => {
  const api: WorkbenchApi = {
    getSnapshot: async (id) => snapshot(id!),
    runActions: { start: async (id) => snapshot(id), act: vi.fn() },
  }
  const openReview = async () => {
    fireEvent.click(await screen.findByRole('button', { name: '交付审阅' }))
    fireEvent.click(screen.getByRole('button', { name: '审阅 T2' }))
    return screen.getByLabelText('修订 SHA · ART-T2')
  }
  const { rerender } = render(<App api={api} projectId="A" />)
  const sha = 'b'.repeat(40)
  fireEvent.change(await openReview(), { target: { value: sha } })
  rerender(<App api={api} projectId="B" />)
  expect(await openReview()).toHaveProperty('value', '')
  rerender(<App api={api} projectId="A" />)
  expect(await openReview()).toHaveProperty('value', sha)
})

it('切换项目后不带出同名计划的未保存编辑', async () => {
  const api: WorkbenchApi = {
    getSnapshot: async (id) => snapshot(id!),
    savePlan: vi.fn(),
  }
  const openPlan = async () => {
    fireEvent.click(await screen.findByRole('button', { name: '任务配置' }))
    return screen.getByLabelText('计划并发上限')
  }
  const { rerender } = render(<App api={api} projectId="A" />)
  fireEvent.change(await openPlan(), { target: { value: '5' } })
  rerender(<App api={api} projectId="B" />)
  expect(await openPlan()).toHaveProperty('value', '2')
  rerender(<App api={api} projectId="A" />)
  expect(await openPlan()).toHaveProperty('value', '5')
  expect(api.savePlan).not.toHaveBeenCalled()
})
