// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { createMockWorkbenchApi } from './api/mockWorkbenchApi'
import { fixture } from './mocks/scenario'

const baseline = fixture.plan_response.plan
const projectId = fixture.project_snapshot.id
const tab = (name: string) => screen.getByRole('button', { name })
const selectTask = (id: string) => within(screen.getByRole('region', { name: '任务列表' }))
  .getByRole('button', { name: new RegExp(`^${id} `) })
const role = () => screen.getByLabelText('执行角色')
const save = () => screen.getByRole('button', { name: '保存计划' })

beforeEach(() => {
  sessionStorage.clear()
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('取消保留旧值，保存完整任务列表后更新编排版本，原运行与甘特图仍绑定旧版本', async () => {
  const api = createMockWorkbenchApi()
  const patch = vi.spyOn(api, 'savePlan')
  const user = userEvent.setup()
  render(<App api={api} />)
  await user.click(await screen.findByRole('button', { name: '任务配置' }))
  await user.click(selectTask('T2'))
  expect(role()).toHaveProperty('value', 'develop')
  fireEvent.change(role(), { target: { value: 'discarded' } })
  await user.click(tab('取消修改'))
  expect(role()).toHaveProperty('value', 'develop')
  expect(patch).not.toHaveBeenCalled()

  fireEvent.change(role(), { target: { value: 'reviewer' } })
  fireEvent.change(screen.getByLabelText('估时（分钟）'), { target: { value: '1.25' } })
  fireEvent.change(screen.getByLabelText('计划并发上限'), { target: { value: '3' } })
  const ancestors = within(screen.getByRole('group', { name: '直接前置任务' }))
  await user.click(ancestors.getByRole('checkbox', { name: /T3/ }))
  await user.click(screen.getByRole('checkbox', { name: '需要人工审核' }))
  await user.click(tab('甘特图'))
  const gantt = within(screen.getByRole('region', { name: '甘特图时间轴' }))
  expect(gantt.getByRole('button', { name: /^T2 ·/ }).getAttribute('aria-pressed')).toBe('true')
  await user.click(tab('任务配置'))
  expect(role()).toHaveProperty('value', 'reviewer')
  await user.click(save())
  expect(await screen.findByText('最新计划 PLAN-01@v2')).toBeTruthy()
  expect(patch).toHaveBeenCalledWith(projectId, baseline.id, {
    expected_version: 1, max_parallel: 3,
    tasks: baseline.tasks.map((task) => task.id === 'T2' ? {
      ...task, role: 'reviewer', estimate_seconds: 75, depends_on: ['T1', 'T3'], review_required: false,
    } : task),
  })
  expect((await api.getSnapshot()).plan?.source.plan).toEqual(baseline)

  await user.click(screen.getByRole('button', { name: /Workflow 编排/ }))
  let details = within(screen.getByRole('region', { name: 'T2 实现预约限制' }))
  expect(details.getByText('reviewer')).toBeTruthy()
  expect(details.getByText('1 分 15 秒')).toBeTruthy()
  expect(details.getByRole('button', { name: 'T3' })).toBeTruthy()
  await user.click(tab('甘特图'))
  expect(screen.getByText('暂无计划排期。')).toBeTruthy()
  expect(screen.getByText('暂无实际用时。')).toBeTruthy()
  await user.click(within(screen.getByRole('navigation', { name: '项目阶段' })).getByRole('button', { name: /开发执行/ }))
  details = within(screen.getByRole('region', { name: 'T2 实现预约限制' }))
  expect(details.getByText('待人工审核')).toBeTruthy()
  expect(details.queryByRole('button', { name: 'T3' })).toBeNull()
  await user.click(tab('甘特图'))
  expect(screen.getByText('计划总时长：20 分钟')).toBeTruthy()
  await user.click(within(screen.getByRole('region', { name: '甘特图时间轴' })).getByRole('button', { name: /^T3 ·/ }))
  await user.click(tab('依赖图'))
  expect(screen.getByRole('region', { name: 'T3 整理使用说明' })).toBeTruthy()
})

it('409 后刷新计划但保留本地修改，显式应用时保留服务端未编辑字段', async () => {
  const api = createMockWorkbenchApi()
  const user = userEvent.setup()
  render(<App api={api} />)
  await user.click(await screen.findByRole('button', { name: '任务配置' }))
  await user.click(selectTask('T2'))
  fireEvent.change(role(), { target: { value: 'local-role' } })
  await api.savePlan!(projectId, baseline.id, {
    expected_version: 1, max_parallel: 5,
    tasks: baseline.tasks.map((task) => task.id === 'T2' ? { ...task, review_required: false } : task),
  })
  await user.click(save())
  expect((await screen.findByRole('alert')).textContent).toContain('已载入最新快照')
  expect(screen.getByRole('alert').textContent).toContain('计划 1 → 2')
  expect(screen.getByRole('alert').textContent).toContain('并发上限 2 → 5')
  expect(screen.getByText('最新计划 PLAN-01@v2')).toBeTruthy()
  expect(role()).toHaveProperty('value', 'local-role')
  expect(save()).toHaveProperty('disabled', true)
  await user.click(tab('将本地修改应用到最新版本'))
  expect(screen.getByLabelText('计划并发上限')).toHaveProperty('value', '5')
  expect(screen.getByRole('checkbox', { name: '需要人工审核' })).toHaveProperty('checked', false)
  await user.click(save())
  expect(await screen.findByText('最新计划 PLAN-01@v3')).toBeTruthy()
  const latest = (await api.getSnapshot()).latestPlan
  expect(latest?.source.plan.max_parallel).toBe(5)
  expect(latest?.tasks.find(({ id }) => id === 'T2')).toMatchObject({ role: 'local-role', reviewRequired: false })
})

it('阻止无效并发值，展示服务端成环路径且不推进版本', async () => {
  const api = createMockWorkbenchApi()
  const patch = vi.spyOn(api, 'savePlan')
  const user = userEvent.setup()
  render(<App api={api} />)
  await user.click(await screen.findByRole('button', { name: '任务配置' }))
  fireEvent.change(screen.getByLabelText('计划并发上限'), { target: { value: '0' } })
  expect(screen.getByText('计划并发上限必须为正整数。')).toBeTruthy()
  expect(save()).toHaveProperty('disabled', true)
  expect(patch).not.toHaveBeenCalled()
  await user.click(tab('取消修改'))
  await user.click(selectTask('T1'))
  const ancestors = within(screen.getByRole('group', { name: '直接前置任务' }))
  expect(ancestors.queryByRole('checkbox', { name: /T1/ })).toBeNull()
  await user.click(ancestors.getByRole('checkbox', { name: /T4/ }))
  await user.click(save())
  expect((await screen.findByRole('alert')).textContent).toContain('T1 → T2 → T4 → T1')
  expect(ancestors.getByRole('checkbox', { name: /T4/ })).toHaveProperty('checked', true)
  expect((await api.getSnapshot()).latestPlan?.ref.version).toBe(1)
})
