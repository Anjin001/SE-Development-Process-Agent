// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { DeliveryReview } from './components/DeliveryReview'
import App from './App'
import { createMockWorkbenchApi } from './api/mockWorkbenchApi'
import { mapWorkbenchSnapshot } from './api/mapWorkbenchSnapshot'
import { WorkbenchApiError } from './api/workbenchApi'
import { fixture } from './mocks/scenario'
import type { Run, RunCommand } from './types/v2'

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals() })

it.each(['approve', 'revise', 'reject'] as const)('回放 T2 审核 %s，保持版本历史并更新 T4', async (decision) => {
  const api = createMockWorkbenchApi()
  const snapshot = await api.getSnapshot()
  const run = snapshot.run!
  const id = snapshot.projectData.id
  const command: RunCommand = { action: 'review', decision, task_id: 'T2', expected_revision: run.revision,
    expected_versions: { 'ART-T2': 1 }, revisions: decision === 'revise' ? { 'ART-T2': { commit_sha: 'a'.repeat(40) } } : {}, note: '审核原因' }
  if (decision === 'reject') await expect(api.runActions!.act(id, run.id, { ...command, note: '' })).rejects.toMatchObject({ status: 422 })
  const result = (await api.runActions!.act(id, run.id, command)).run!
  const t2 = result.tasks.find(({ task_id }) => task_id === 'T2')!
  expect(t2.state).toBe(decision === 'reject' ? 'failed' : 'done')
  expect(t2.review_records.at(-1)?.decision).toBe(decision)
  expect(result.tasks.find(({ task_id }) => task_id === 'T4')?.state).toBe(decision === 'reject' ? 'blocked' : 'ready')
  if (decision === 'revise') expect(t2.artifacts.map(({ version, based_on_version }) => [version, based_on_version])).toEqual([[1, null], [2, 1]])
  await expect(api.runActions!.act(id, run.id, command)).rejects.toMatchObject({ status: 409 })
})

it('覆盖所有待审版本、仅发送变更，并保留冲突前的编辑', () => {
  const sample = fixture.review_snapshot
  const run: Run = { ...sample, tasks: sample.tasks.map((task) => task.task_id !== 'T2' ? task : {
    ...task, artifacts: [...task.artifacts, { ...task.artifacts[0], id: 'ART-TEXT', output_id: 'OUT-02', kind: 'text',
      commit_sha: null, changed_paths: [], content: '原创文本' }],
  }) }
  const submit = vi.fn(async () => false)
  const props = { selectedId: 'T2', onSelect: vi.fn(), submit, busy: false }
  const { rerender } = render(<DeliveryReview run={run} {...props} />)
  expect(screen.getByRole('button', { name: '退回' })).toHaveProperty('disabled', true)
  fireEvent.change(screen.getByLabelText('修订内容 · ART-TEXT'), { target: { value: '人工修订' } })
  fireEvent.click(screen.getByRole('button', { name: '修订并批准' }))
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({
    expected_versions: { 'ART-T2': 1, 'ART-TEXT': 1 }, revisions: { 'ART-TEXT': { content: '人工修订' } },
  }))
  rerender(<DeliveryReview {...props} run={{ ...run, revision: run.revision + 1, tasks: run.tasks.map((task) =>
    ({ ...task, artifacts: task.artifacts.map((item) => item.id === 'ART-TEXT' ? { ...item, version: 2 } : item) })) }} />)
  expect(screen.getByLabelText('修订内容 · ART-TEXT')).toHaveProperty('value', '人工修订')
  expect(screen.getByRole('button', { name: '修订并批准' })).toHaveProperty('disabled', true)
})

it('审核提交遇到 409 会刷新快照，保留草稿且不自动重试', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  const { project_snapshot: project, plan_response: plan, review_snapshot: run } = fixture
  const latest: Run = { ...run, revision: run.revision + 1, tasks: run.tasks.map((task) =>
    ({ ...task, artifacts: task.artifacts.map((item) => item.id === 'ART-T2' ? { ...item, version: 2 } : item) })) }
  const snapshot = mapWorkbenchSnapshot(project, plan, run, [])
  const getSnapshot = vi.fn().mockResolvedValueOnce(snapshot).mockResolvedValue(mapWorkbenchSnapshot(project, plan, latest, []))
  const act = vi.fn().mockRejectedValue(new WorkbenchApiError('审核版本冲突', 'VERSION_CONFLICT', 409))
  render(<App api={{ getSnapshot, runActions: { act, start: vi.fn() } }} />)
  fireEvent.click(await screen.findByRole('button', { name: '交付审阅' }))
  fireEvent.click(screen.getByRole('button', { name: '审阅 T2' }))
  const sha = 'b'.repeat(40)
  fireEvent.change(screen.getByLabelText('修订 SHA · ART-T2'), { target: { value: sha } })
  fireEvent.click(screen.getByRole('button', { name: '修订并批准' }))
  expect((await screen.findByRole('alert')).textContent).toContain('已载入最新快照')
  expect(screen.getByLabelText('修订 SHA · ART-T2')).toHaveProperty('value', sha)
  expect(screen.getByRole('button', { name: '修订并批准' })).toHaveProperty('disabled', true)
  expect(act).toHaveBeenCalledOnce()
  expect(getSnapshot).toHaveBeenLastCalledWith(project.id)
})
