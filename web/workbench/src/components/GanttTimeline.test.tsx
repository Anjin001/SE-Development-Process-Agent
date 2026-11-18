// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { mapWorkbenchSnapshot } from '../api/mapWorkbenchSnapshot'
import { fixture } from '../mocks/scenario'
import type { Attempt } from '../types/v2'
import { GanttTimeline } from './GanttTimeline'

const { project_snapshot: project, plan_response: response, review_snapshot: run } = fixture
afterEach(cleanup)

it('按分钟刻度展示原排期、关键任务、真实失败耗时并支持选中', async () => {
  const attempt: Attempt = {
    id: 'T2.2', number: 2, state: 'failed', created_at: '2026-11-17T10:02:00Z',
    started_at: '2026-11-17T10:02:00Z', ended_at: '2026-11-17T10:02:20Z', error: null, input_refs: [],
  }
  const withFailure = { ...run, tasks: run.tasks.map((task) =>
    task.task_id === 'T2' ? { ...task, attempts: [...task.attempts, attempt] } : task) }
  const plan = mapWorkbenchSnapshot(project, response, withFailure, []).plan!
  const onSelect = vi.fn()
  render(<GanttTimeline plan={plan} startedAt={run.started_at} selectedId="T1" onSelect={onSelect} />)
  expect(screen.getByText('计划总时长：20 分钟')).toBeTruthy()
  expect(screen.getByText('0 分')).toBeTruthy()
  expect(screen.getByText('20 分')).toBeTruthy()
  const t2 = screen.getByRole('button', { name: /^T2 ·/ })
  expect(within(t2).getByText(/关键路径/)).toBeTruthy()
  expect(within(t2).getByText('第 1 次 · 成功 · 40 秒')).toBeTruthy()
  expect(within(t2).getByText('第 2 次 · 失败 · 20 秒')).toBeTruthy()
  expect(t2.querySelector('.gantt-failed')).toBeTruthy()
  expect(t2.querySelector('.gantt-critical')).toBeTruthy()
  expect(within(screen.getByRole('button', { name: /^T3 ·/ })).queryByText(/关键路径/)).toBeNull()
  await userEvent.setup().click(t2)
  expect(onSelect).toHaveBeenCalledWith('T2')
})

it('空排期可保留有时间的实际尝试，缺失起止时间不补造耗时', () => {
  const plan = mapWorkbenchSnapshot(project, { ...response, schedule: null }, run, []).plan!
  const { container, rerender } = render(<GanttTimeline plan={plan} startedAt={run.started_at} onSelect={vi.fn()} />)
  expect(screen.getByText('暂无计划排期。')).toBeTruthy()
  expect(container.querySelectorAll('.gantt-planned')).toHaveLength(0)
  expect(container.querySelectorAll('.gantt-actual')).toHaveLength(3)
  expect(within(screen.getByRole('button', { name: /^T4 ·/ })).getByText('暂无实际执行记录')).toBeTruthy()
  const incomplete: typeof run = {
    ...run, started_at: null, tasks: run.tasks.map((task) =>
      ({ ...task, attempts: task.attempts.map((attempt) => ({ ...attempt, started_at: null, ended_at: null })) })),
  }
  const empty = mapWorkbenchSnapshot(project, { ...response, schedule: null }, incomplete, []).plan!
  rerender(<GanttTimeline plan={empty} startedAt={incomplete.started_at} onSelect={vi.fn()} />)
  expect(screen.getByText('暂无实际用时。')).toBeTruthy()
  expect(screen.getAllByText(/实际时间不完整/)).toHaveLength(3)
  expect(container.querySelectorAll('.gantt-actual')).toHaveLength(0)
})
