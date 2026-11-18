// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { mapWorkbenchSnapshot } from './api/mapWorkbenchSnapshot'
import { createMockWorkbenchApi } from './api/mockWorkbenchApi'
import { fixture } from './mocks/scenario'

beforeEach(() => {
  sessionStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const fixtureProject = fixture.project_snapshot.id
const sidebar = () => within(screen.getByRole('complementary'))
const button = (name: string | RegExp) => screen.getByRole('button', { name })
const runPanel = () => within(sidebar().getByRole('region', { name: '执行控制' }))
const taskList = () => within(screen.getByRole('region', { name: '任务列表' }))

it('在工作台启动手动回放，暂停后收取回报，与任务视图及活动同步', async () => {
  let clock = Date.parse('2026-11-17T10:00:00Z')
  const api = createMockWorkbenchApi({ now: () => clock })
  const act = vi.spyOn(api.runActions!, 'act')
  const user = userEvent.setup()
  render(<App api={api} monitorInterval={100} />)
  await user.click(await screen.findByRole('button', { name: '载入执行控制演示' }))
  await user.click(button('确认工作区并启动'))
  await user.click(await screen.findByRole('button', { name: '单步派发一批' }))
  await user.click(button('暂停派发'))
  clock += 700
  const tracked = await sidebar().findByRole('button', { name: 'T2 · 待补充输入' })
  await user.click(tracked)
  expect(runPanel().getByRole('button', { name: '单步派发一批' })).toHaveProperty('disabled', true)
  expect(sidebar().getByText(/证据：OPEN-01、PLAN-01@v1/)).toBeTruthy()
  expect(sidebar().getByText(/建议：选择 T2/)).toBeTruthy()
  const input = runPanel().getByRole('textbox', { name: /OPEN-01/ })
  expect(runPanel().getAllByRole('textbox')).toHaveLength(1)
  fireEvent.change(input, { target: { value: '  LAB-DEMO-07  ' } })
  await user.click(button('提交补充输入'))
  expect(act.mock.calls.at(-1)?.[2]).toMatchObject({
    action: 'supply-input', task_id: 'T2', values: { 'OPEN-01': 'LAB-DEMO-07' }, expected_revision: expect.any(Number),
  })
  await user.click(button('继续派发'))
  await user.click(button('单步派发一批'))
  clock += 500
  await user.click(await sidebar().findByRole('button', { name: 'T4 · 受阻' }))
  expect(screen.getByText(/UPSTREAM_FAILED · T2/)).toBeTruthy()
  expect(taskList().getByRole('button', { name: /^T3 / }).textContent).toContain('执行中')
  clock += 800
  await sidebar().findByText('2 / 4 项已交付')
  await user.click(sidebar().getByRole('button', { name: 'T2 · 失败' }))
  await user.click(button('重试失败任务 T2'))
  clock += 250
  await taskList().findByRole('button', { name: /^T2 .*就绪$/ })
  await user.click(button('单步派发一批'))
  clock += 600
  await sidebar().findByRole('button', { name: 'T2 · 待人工审核' })
  await user.click(button('甘特图'))
  const bar = within(screen.getByRole('region', { name: '甘特图时间轴' })).getByRole('button', { name: /^T2 ·/ })
  expect(bar.textContent).toContain('第 1 次 · 失败')
  expect(bar.getAttribute('aria-pressed')).toBe('true')
  await user.click(screen.getByText('活动记录', { exact: true }))
  expect(screen.getAllByText('任务失败')).toHaveLength(1)
  expect(screen.getByText('产物已提交')).toBeTruthy()
}, 10000)

it('断线恢复后从原游标重读事件并去重，保留选择和同步详情', async () => {
  const base = mapWorkbenchSnapshot(fixture.project_snapshot, fixture.plan_response, fixture.review_snapshot, [])
  let connected = false
  const event = fixture.approval_event
  const getEvents = vi.fn(async (_project: string, _run: string, after: number) => {
    if (!connected) throw new Error('网络暂不可用')
    return { items: [event, event], next_after: Math.max(after, event.seq), has_more: false }
  })
  const getRun = vi.fn(async () => fixture.review_response)
  const api = { getSnapshot: async () => base, runReads: { getEvents, getRun } }
  const user = userEvent.setup()
  render(<App api={api} monitorInterval={80} />)
  const list = within(await screen.findByRole('region', { name: '任务列表' }))
  await user.click(list.getByRole('button', { name: /^T2 / }))
  expect((await sidebar().findByRole('alert')).textContent).toContain('保留最近快照')
  connected = true
  fireEvent(window, new Event('online'))
  expect(await sidebar().findByText('运行监控已连接')).toBeTruthy()
  const detail = within(screen.getByRole('region', { name: 'T2 实现预约限制' }))
  expect(detail.getByText('ART-T2@v2')).toBeTruthy()
  expect(getRun).toHaveBeenCalledOnce()
  expect(getEvents.mock.calls.filter(([, , after]) => after === 9).length).toBeGreaterThan(1)
  await user.click(screen.getByText('活动记录', { exact: true }))
  expect(screen.getAllByText('产物已批准')).toHaveLength(1)
})

it('409 使用新修订号刷新，保留选择，重复点击只发送一次继续操作', async () => {
  const api = createMockWorkbenchApi()
  const user = userEvent.setup()
  const act = vi.spyOn(api.runActions!, 'act')
  render(<App api={api} monitorInterval={60000} />)
  const list = within(await screen.findByRole('region', { name: '任务列表' }))
  await user.click(list.getByRole('button', { name: /^T2 / }))
  const run = (await api.getSnapshot()).run!
  await api.runActions!.act(fixtureProject, run.id, { action: 'pause', expected_revision: run.revision })
  act.mockClear()
  await user.click(button('暂停派发'))
  expect((await screen.findByRole('alert')).textContent).toContain('已载入最新快照')
  expect(screen.getByRole('region', { name: 'T2 实现预约限制' })).toBeTruthy()
  expect(act).toHaveBeenCalledOnce()
  const resume = button('继续派发')
  fireEvent.click(resume)
  fireEvent.click(resume)
  await screen.findByRole('button', { name: '暂停派发' })
  expect(act).toHaveBeenCalledTimes(2)
  expect(act.mock.calls[1][2]).toEqual({ action: 'resume', expected_revision: run.revision + 1 })
})

it('自动模式不显示单步，手动刷新和重载恢复当前任务', async () => {
  const autoRun = { ...fixture.review_snapshot, mode: 'auto' as const }
  const snapshot = mapWorkbenchSnapshot(fixture.project_snapshot, fixture.plan_response, autoRun, [])
  const getSnapshot = vi.fn(async () => snapshot)
  const api = { getSnapshot }
  const user = userEvent.setup()
  const mounted = render(<App api={api} />)
  const list = within(await screen.findByRole('region', { name: '任务列表' }))
  await user.click(list.getByRole('button', { name: /^T3 / }))
  expect(runPanel().getByText('自动')).toBeTruthy()
  expect(runPanel().queryByRole('button', { name: '单步派发一批' })).toBeNull()
  await user.click(button('刷新运行'))
  expect(getSnapshot).toHaveBeenLastCalledWith(fixtureProject)
  expect(screen.getByRole('region', { name: 'T3 整理使用说明' })).toBeTruthy()
  mounted.unmount()
  render(<App api={api} />)
  expect(await screen.findByRole('region', { name: 'T3 整理使用说明' })).toBeTruthy()
})
