// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { mapWorkbenchSnapshot } from './api/mapWorkbenchSnapshot'
import { fixture } from './mocks/scenario'
import { createMockWorkbenchApi } from './api/mockWorkbenchApi'
import { WorkbenchApiError } from './api/workbenchApi'
import type { ProjectSnapshot, Specification } from './types/v2'

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

describe('工作台', () => {
  it('浏览历史和后续阶段时保留示例项目的实际进度', async () => {
    const user = userEvent.setup()
    render(<App />)

    const navigation = within(await screen.findByRole('navigation', { name: '项目阶段' }))
    expect(navigation.getAllByRole('button')).toHaveLength(6)
    expect(navigation.getByRole('button', { name: /开发执行/ }).getAttribute('aria-current')).toBe('step')

    await user.click(screen.getByRole('button', { name: '交付审阅' }))
    expect(screen.getByRole('heading', { name: '交付审阅' })).toBeTruthy()
    expect(screen.getByText('第 6 / 6 阶段')).toBeTruthy()

    await user.click(navigation.getByRole('button', { name: /Workflow 编排/ }))
    expect(screen.getByRole('button', { name: '确认工作区并启动' })).toHaveProperty('disabled', true)
    expect(screen.getByText('第 6 / 6 阶段')).toBeTruthy()

    await user.click(navigation.getByRole('button', { name: /需求录入/ }))
    expect(screen.getByRole('textbox', { name: '编辑需求原文' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '交付审阅' })).toBeNull()
  })

  it('切换编排视图并展开示例活动记录', async () => {
    const user = userEvent.setup()
    const { project_snapshot, plan_response, review_response, approval_event } = fixture
    render(<App api={{ getSnapshot: async () => mapWorkbenchSnapshot(project_snapshot, plan_response, review_response, [approval_event]) }} />)
    expect(await screen.findByRole('heading', { name: 'T1 定义设备状态' })).toBeTruthy()

    await user.click(screen.getByRole('button', { name: '甘特图' }))
    expect(screen.getByRole('heading', { name: '甘特图' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '依赖图' }))
    expect(screen.getByRole('heading', { name: 'T1 定义设备状态' })).toBeTruthy()

    const summary = screen.getByText('活动记录').closest('summary')!
    expect(summary.parentElement).toHaveProperty('open', false)
    await user.click(summary)
    expect(summary.parentElement).toHaveProperty('open', true)
    expect(screen.getByText('产物已批准')).toBeTruthy()
    expect(screen.getByText('RUN-01 · T2 · T2.1')).toBeTruthy()
  })

  it('展示审核阻塞及计划、规格版本', async () => {
    const user = userEvent.setup()
    render(<App />)
    const details = within(await screen.findByRole('region', { name: 'T4 集成预约规则' }))
    expect(details.getByText('等待前置')).toBeTruthy()
    expect(details.getByText(/UPSTREAM_REVIEW · T2/)).toBeTruthy()
    expect(details.getByText('5 分钟 · 关键路径')).toBeTruthy()
    expect(details.getByText('SPEC-01@v1')).toBeTruthy()
    expect(screen.getByText('PLAN-01@v1')).toBeTruthy()
    expect(details.getByText('暂无执行尝试。')).toBeTruthy()
    expect(details.getByText('暂无实际产物。')).toBeTruthy()

    const blockers = within(details.getByRole('region', { name: '阻塞原因' }))
    await user.click(blockers.getByRole('button', { name: 'T2' }))
    const upstream = within(screen.getByRole('region', { name: 'T2 实现预约限制' }))
    expect(upstream.getByText('待人工审核')).toBeTruthy()
    expect(upstream.getByText('REQ-02')).toBeTruthy()
    expect(upstream.getByText('DEC-01')).toBeTruthy()
    expect(upstream.getAllByText('app/reservations.py').length).toBeGreaterThan(1)
    expect(upstream.getByText('T2.1')).toBeTruthy()
    expect(upstream.getByText('ART-T2@v1')).toBeTruthy()
    expect(upstream.getByText('草稿')).toBeTruthy()
    expect(upstream.getByText('3333333333333333333333333333333333333333')).toBeTruthy()

    const list = within(screen.getByRole('region', { name: '任务列表' }))
    expect(list.getByRole('button', { name: /T2 实现预约限制/ }).getAttribute('aria-pressed')).toBe('true')
    await user.click(list.getByRole('button', { name: /T3 整理使用说明/ }))
    expect(screen.getByRole('region', { name: 'T3 整理使用说明' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Workflow 编排/ }))
    expect(screen.getByRole('region', { name: 'T3 整理使用说明' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '甘特图' }))
    await user.click(screen.getByRole('button', { name: '依赖图' }))
    expect(screen.getByRole('region', { name: 'T3 整理使用说明' })).toBeTruthy()
  })

  it('运行阶段未取得计划时展示依赖图空态', async () => {
    const { project_snapshot, review_snapshot } = fixture
    render(<App api={{ getSnapshot: async () => mapWorkbenchSnapshot(project_snapshot, null, review_snapshot, []) }} />)
    expect(await screen.findByText('暂无可显示的计划任务。')).toBeTruthy()
    expect(screen.queryByRole('region', { name: '任务依赖图' })).toBeNull()
  })

  it('缺少排期时保留依赖图并隐藏关键路径标记', async () => {
    const { project_snapshot, plan_response, review_snapshot } = fixture
    const snapshot = mapWorkbenchSnapshot(project_snapshot, { ...plan_response, schedule: null }, review_snapshot, [])
    render(<App api={{ getSnapshot: async () => snapshot }} />)
    expect(await screen.findByRole('region', { name: '任务依赖图' })).toBeTruthy()
    expect(screen.getByText('暂无排期')).toBeTruthy()
    expect(screen.queryByText(/排期标记的关键路径任务/)).toBeNull()
  })

  it('快照加载失败时显示错误', async () => {
    render(<App api={{ getSnapshot: async () => { throw new Error('unavailable') } }} />)
    expect(screen.getByRole('status').textContent).toContain('正在载入')
    expect((await screen.findByRole('alert')).textContent).toContain('无法载入')
  })

  it('提交需求新版本，等待 A 组完成并重试 B 组，然后编辑和确认融合规格', async () => {
    let clock = 0
    const api = createMockWorkbenchApi({ now: () => clock })
    const user = userEvent.setup()
    render(<App api={api} />)
    const navigation = within(await screen.findByRole('navigation', { name: '项目阶段' }))
    await user.click(navigation.getByRole('button', { name: /需求录入/ }))
    expect(screen.getByText('REQSET-01@v1')).toBeTruthy()
    await user.click(screen.getByText('原文：设备预约 · 第 2 行'))
    expect(screen.getByText(fixture.project_snapshot.requirement.items[0].text, { selector: 'pre' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '提交需求版本' }))
    expect(await screen.findByText('REQSET-01@v2')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '查看方案赛马' }))
    await user.click(screen.getByRole('button', { name: '启动 A/B 赛马' }))
    expect(await screen.findByText(/赛马进行中/)).toBeTruthy()
    const groupA = within(screen.getByRole('article', { name: 'A 组方案' }))
    const groupB = within(screen.getByRole('article', { name: 'B 组方案' }))
    expect(groupA.queryByText('统一设备状态')).toBeNull()
    expect(groupB.queryByText('先限制预约入口')).toBeNull()

    clock = 700
    expect(await groupA.findByText('统一设备状态', {}, { timeout: 2000 })).toBeTruthy()
    expect(groupA.getByText('关联需求：REQ-01')).toBeTruthy()
    for (const dimension of ['(value)', '(feasibility)', '(cost)']) expect(groupA.getByText(dimension)).toBeTruthy()
    expect(groupB.getByText('进行中')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '进入规格评审' })).toBeNull()
    await user.click(navigation.getByRole('button', { name: /评审与整合/ }))
    expect(screen.getByRole('heading', { name: '规格尚未就绪' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '确认规格' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '查看赛马进度' }))

    clock = 1900
    const retry = await screen.findByRole('button', { name: '重试失败组' }, { timeout: 2000 })
    expect(screen.getByText(/MOCK_TEMPORARY_FAILURE/)).toBeTruthy()
    expect(screen.queryByText('先限制预约入口')).toBeNull()
    await user.click(retry)
    expect(await screen.findByText(/赛马进行中/)).toBeTruthy()
    expect(screen.getByText('统一设备状态')).toBeTruthy()
    clock = 4000
    expect(await screen.findByText('先限制预约入口', {}, { timeout: 2000 })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '进入规格评审' }))

    await user.selectOptions(screen.getByLabelText('采用方案'), 'merge')
    await user.click(screen.getByRole('button', { name: '生成待确认规格' }))
    expect(await screen.findByText('SPEC-01@v2 · 未确认')).toBeTruthy()
    expect(screen.getByText('绑定需求：REQSET-01@v2')).toBeTruthy()
    expect(screen.getByText('DEC-01 · 列表与预约')).toBeTruthy()
    expect(screen.getByText('未解决 · 阻塞相关任务')).toBeTruthy()
    const markdown = '# 可验收规格\n保留停用状态。'
    fireEvent.change(screen.getByLabelText('编辑规格原文'), { target: { value: markdown } })
    expect(screen.getByRole('button', { name: '确认规格' })).toHaveProperty('disabled', true)
    await user.click(screen.getByRole('button', { name: '保存草稿' }))
    expect(await screen.findByText('SPEC-01@v3 · 未确认')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '确认规格' }))
    expect(await screen.findByText('SPEC-01@v3 · 已确认')).toBeTruthy()
    expect(screen.getByLabelText('已确认规格原文')).toHaveProperty('value', markdown)
    expect(screen.getByLabelText('已确认规格原文')).toHaveProperty('readOnly', true)
    expect(screen.getByText('第 4 / 6 阶段')).toBeTruthy()
  }, 10000)

  it('保存发生版本冲突时刷新快照并保留未提交的规格 Markdown', async () => {
    const source = fixture.project_snapshot
    const draft: Specification = { ...source.specification!, status: 'draft', confirmed_by: null, confirmed_at: null }
    const project: ProjectSnapshot = { ...source, stage: 'specification', specification: draft, latest_plan_ref: null, runs: [] }
    const latestDraft = { ...draft, version: draft.version + 1, markdown: '# 另一成员的修订' }
    let current = mapWorkbenchSnapshot(project, null, null, [])
    const save = vi.fn(async () => {
      current = mapWorkbenchSnapshot({ ...project, specification: latestDraft }, null, null, [])
      throw new WorkbenchApiError('版本已变', 'VERSION_CONFLICT', 409)
    })
    const mock = createMockWorkbenchApi()
    const user = userEvent.setup()
    render(<App api={{
      getSnapshot: async () => current,
      projectActions: { ...mock.projectActions!, saveSpecification: save },
    }} />)
    const editor = await screen.findByLabelText('编辑规格原文')
    fireEvent.change(editor, { target: { value: '# 本地编辑' } })
    await user.click(screen.getByRole('button', { name: '保存草稿' }))
    expect((await screen.findByRole('alert')).textContent).toContain('已载入最新快照')
    expect(screen.getByText('SPEC-01@v2 · 未确认')).toBeTruthy()
    expect(editor).toHaveProperty('value', '# 本地编辑')
    expect(screen.getByRole('button', { name: '保存草稿' })).toHaveProperty('disabled', true)
    expect(save).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: '以最新规格版本继续编辑' }))
    expect(screen.getByRole('button', { name: '保存草稿' })).toHaveProperty('disabled', false)
  })
})
