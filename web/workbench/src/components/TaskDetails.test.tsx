// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import example from '../../../../examples/v2/scenario.json'
import { mapWorkbenchSnapshot } from '../api/mapWorkbenchSnapshot'
import { fixture } from '../mocks/scenario'
import type { Attempt, Run } from '../types/v2'
import { TaskDetails } from './TaskDetails'

const { project_snapshot: project, plan_response: response, review_snapshot: run, review_response: reviewed } = fixture
afterEach(cleanup)

function showDetails(id: string, runtime: Run) {
  const plan = mapWorkbenchSnapshot(project, response, runtime, []).plan!
  const task = plan.tasks.find((entry) => entry.id === id)!
  render(<TaskDetails task={task} tasks={plan.tasks} specRef={plan.specRef} onSelect={vi.fn()} />)
}

it('显示历史草稿、获批修订的版本、Git SHA 和审核备注', () => {
  showDetails('T2', reviewed)
  const artifacts = within(screen.getByRole('region', { name: '实际产物与审核' }))
  expect(artifacts.getByText('ART-T2@v2')).toBeTruthy()
  expect(artifacts.getAllByText('ART-T2@v1')).toHaveLength(2)
  expect(artifacts.getByText('草稿')).toBeTruthy()
  expect(artifacts.getByText('已批准')).toBeTruthy()
  expect(artifacts.getByText('4444444444444444444444444444444444444444')).toBeTruthy()
  expect(artifacts.getByText(/批准修订版 · lead-demo/)).toBeTruthy()
  expect(artifacts.getByText('补齐停用状态分支')).toBeTruthy()
})

it('显示服务端返回的失败原因和空时间，保留 failed 状态', () => {
  const result = example.worker_failure
  const attempt: Attempt = {
    id: result.attempt_id, number: 1, state: 'failed', created_at: result.ended_at,
    started_at: null, ended_at: result.ended_at, error: result.error, input_refs: [],
  }
  showDetails('T4', { ...run, tasks: run.tasks.map((task) =>
    task.task_id === 'T4' ? { ...task, state: 'failed', blockers: [], attempts: [attempt] } : task),
  })
  expect(screen.getByText('失败', { exact: true })).toBeTruthy()
  expect(screen.getByText('执行失败')).toBeTruthy()
  expect(screen.getByText('未开始')).toBeTruthy()
  expect(screen.getByText(/上游提交无法合并（MERGE_CONFLICT）/)).toBeTruthy()
  expect(screen.getByText('暂无实际产物。')).toBeTruthy()
})

it('有上游失败时按快照标记受阻，并注明关联任务', () => {
  showDetails('T4', { ...run, tasks: run.tasks.map((task) =>
    task.task_id === 'T4' ? {
      ...task, state: 'blocked', blockers: [{ code: 'UPSTREAM_FAILED', ref_id: 'T2', message: '上游任务失败' }],
    } : task),
  })
  expect(screen.getByText('受阻', { exact: true })).toBeTruthy()
  expect(screen.queryByText('等待前置', { exact: true })).toBeNull()
  const blockers = within(screen.getByRole('region', { name: '阻塞原因' }))
  expect(blockers.getByText(/UPSTREAM_FAILED · T2/)).toBeTruthy()
  expect(blockers.getByRole('button', { name: 'T2' })).toBeTruthy()
})
