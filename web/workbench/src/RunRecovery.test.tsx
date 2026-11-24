// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { mapWorkbenchSnapshot } from './api/mapWorkbenchSnapshot'
import type { WorkbenchApi } from './api/workbenchApi'
import { fixture } from './mocks/scenario'

const { project_snapshot: project, plan_response: plan, review_snapshot: run, review_response: approved, approval_event: event } = fixture
const snapshot = (value = run, projectId = project.id) => mapWorkbenchSnapshot({
  ...project, id: projectId, requirement: { ...project.requirement, markdown: '# ' + projectId },
}, plan, value, [])
const sidebar = () => within(screen.getByRole('complementary'))

beforeEach(() => {
  sessionStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('断线或事件缺页保留画面与游标，恢复项目和运行后补齐活动', async () => {
  let service: 'offline' | 'gap' | 'ready' = 'offline'
  const getSnapshot = vi.fn(async () => service === 'offline' ? snapshot() : snapshot(approved))
  const getEvents = vi.fn(async (_project: string, _run: string, after: number) => {
    if (service === 'offline') throw new TypeError('服务连接断开')
    if (service === 'gap') return { items: [], next_after: 10, has_more: false }
    return { items: after < 10 ? [event] : [], next_after: 10, has_more: false }
  })
  const getRun = vi.fn(async () => approved)
  render(<App api={{ getSnapshot, runReads: { getEvents, getRun } }} monitorInterval={40} />)
  await screen.findByRole('complementary')
  expect((await sidebar().findByRole('alert')).textContent).toContain('连接断开')
  expect(sidebar().queryByText('ART-T2@v2')).toBeNull()
  service = 'gap'
  fireEvent(window, new Event('online'))
  await waitFor(() => expect(getSnapshot).toHaveBeenLastCalledWith(project.id))
  await waitFor(() => expect(sidebar().getByRole('alert').textContent).toContain('不连续'))
  expect(getEvents.mock.calls.every(([, , after]) => after === 9)).toBe(true)
  service = 'ready'
  fireEvent(window, new Event('online'))
  expect(await sidebar().findByText('运行监控已连接')).toBeTruthy()
  expect(getRun).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByText('活动记录', { exact: true }))
  expect(await screen.findByText('产物已批准')).toBeTruthy()
})

it('手动刷新拒绝版本回退，包括相同修订号的冲突运行状态', async () => {
  const paused = { ...approved, dispatch: 'paused' as const }
  const getSnapshot = vi.fn()
    .mockResolvedValueOnce(snapshot(paused))
    .mockResolvedValueOnce(snapshot(run))
    .mockResolvedValueOnce(snapshot({ ...paused, dispatch: 'active' }))
  render(<App api={{ getSnapshot, runActions: { start: vi.fn(), act: vi.fn() } }} />)
  expect(await screen.findByRole('button', { name: '继续派发' })).toBeTruthy()
  for (let count = 2; count <= 3; count += 1) {
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '刷新运行' })))
    expect(getSnapshot).toHaveBeenCalledTimes(count)
    expect(screen.getByRole('button', { name: '继续派发' })).toBeTruthy()
  }
})

it('切换项目后废弃旧项目的恢复快照和后续轮询', async () => {
  let finish!: (value: ReturnType<typeof snapshot>) => void
  const recovered = new Promise<ReturnType<typeof snapshot>>((resolve) => { finish = resolve })
  let first = true
  const getSnapshot = vi.fn(async (id?: string) => {
    if (id === 'A' && !first) return recovered
    first = false
    return snapshot(run, id)
  })
  const getEvents = vi.fn(async (_project: string, _run: string, after: number) =>
    ({ items: [], next_after: after, has_more: false }))
  const api: WorkbenchApi = { getSnapshot, runReads: { getEvents, getRun: vi.fn() } }
  const { rerender } = render(<App api={api} projectId="A" monitorInterval={1000} />)
  await screen.findByRole('heading', { name: 'A' })
  fireEvent(window, new Event('online'))
  await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2))
  rerender(<App api={api} projectId="B" monitorInterval={1000} />)
  await screen.findByRole('heading', { name: 'B' })
  await act(async () => finish(snapshot(approved, 'A')))
  expect(screen.queryByRole('heading', { name: 'A' })).toBeNull()
  expect(getEvents.mock.calls.some(([id]) => id === 'A')).toBe(false)
})

it('切换数据源或卸载后，旧恢复请求不能启动后续事件读取', async () => {
  let finish!: (value: ReturnType<typeof snapshot>) => void
  const recovered = new Promise<ReturnType<typeof snapshot>>((resolve) => { finish = resolve })
  const getSnapshot = vi.fn().mockResolvedValueOnce(snapshot()).mockReturnValueOnce(recovered)
  const getEvents = vi.fn()
  const mock: WorkbenchApi = { getSnapshot, exampleMarkdown: '# Mock', runReads: { getEvents, getRun: vi.fn() } }
  const real: WorkbenchApi = { getSnapshot: async () => snapshot() }
  const { rerender, unmount } = render(<App api={mock} monitorInterval={1000} />)
  await screen.findByText('模拟演示')
  fireEvent(window, new Event('online'))
  await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2))
  rerender(<App api={real} monitorInterval={1000} />)
  await screen.findByRole('heading', { name: project.id })
  expect(screen.queryByText('模拟演示')).toBeNull()
  unmount()
  await act(async () => finish(snapshot(approved)))
  fireEvent(window, new Event('online'))
  expect(getEvents).not.toHaveBeenCalled()
  expect(getSnapshot).toHaveBeenCalledTimes(2)
})
