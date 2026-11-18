import { expect, it } from 'vitest'
import { createMockWorkbenchApi } from '../api/mockWorkbenchApi'
import type { RunCommand } from '../types/v2'

it('手动回放启动、单步、暂停回报、补输入、独立分支、失败阻塞与重试', async () => {
  let clock = Date.parse('2026-11-17T10:00:00Z')
  const api = createMockWorkbenchApi({ now: () => clock })
  const actions = api.runActions!
  const project = await api.loadRunDemo!()
  const id = project.projectData.id
  const request = { mode: 'manual' as const, plan_ref: project.latestPlan!.ref }
  let run = (await actions.start(id, request)).run!
  const act = async (fields: RunCommand) => { run = (await actions.act(id, run.id, fields)).run! }
  const read = async (advance: number) => { clock += advance; run = await api.runReads!.getRun(id, run.id) }
  const task = (taskId: string) => run.tasks.find(({ task_id }) => task_id === taskId)!
  await expect(actions.start(id, request)).rejects.toMatchObject({ code: 'INVALID_STATE' })
  const step = { action: 'step' as const, expected_revision: run.revision }
  await act(step)
  expect(task('T1').state).toBe('running')
  await expect(actions.act(id, run.id, step)).rejects.toMatchObject({ code: 'VERSION_CONFLICT' })
  await act({ action: 'pause', expected_revision: run.revision })
  await read(600)
  expect(run.dispatch).toBe('paused')
  expect(task('T1').state).toBe('done')
  expect(task('T2').missing_input_ids).toEqual(['OPEN-01'])
  expect(task('T3').state).toBe('ready')
  await expect(act({ action: 'step', expected_revision: run.revision })).rejects.toMatchObject({ code: 'INVALID_STATE' })
  await expect(act({ action: 'supply-input', expected_revision: run.revision, task_id: 'T2', values: { 'OPEN-02': 'bad' } }))
    .rejects.toMatchObject({ code: 'INVALID_REFERENCE' })
  await act({ action: 'supply-input', expected_revision: run.revision, task_id: 'T2', values: { 'OPEN-01': 'LAB-DEMO-07' } })
  await act({ action: 'resume', expected_revision: run.revision })
  await act({ action: 'step', expected_revision: run.revision })
  expect(task('T2').state).toBe('running')
  expect(task('T3').state).toBe('running')
  await read(500)
  expect(task('T2').state).toBe('failed')
  expect(task('T4').blockers).toEqual([expect.objectContaining({ code: 'UPSTREAM_FAILED', ref_id: 'T2' })])
  expect(task('T3').state).toBe('running')
  await act({ action: 'pause', expected_revision: run.revision })
  await read(800)
  expect(task('T3').state).toBe('done')
  expect(task('T4').state).toBe('blocked')
  await act({ action: 'retry', expected_revision: run.revision, task_id: 'T2' })
  expect(task('T2').state).toBe('waiting')
  expect(task('T4').state).toBe('waiting')
  await read(220)
  expect(task('T2').state).toBe('ready')
  await act({ action: 'resume', expected_revision: run.revision })
  await act({ action: 'step', expected_revision: run.revision })
  await read(500)
  expect(task('T2').state).toBe('review')
  expect(task('T2').attempts.map(({ state }) => state)).toEqual(['failed', 'succeeded'])
  expect(task('T4').blockers[0].code).toBe('UPSTREAM_REVIEW')
})

it('自动回放拒绝手动单步，暂停不会派发已就绪独立任务', async () => {
  let clock = 0
  const api = createMockWorkbenchApi({ now: () => clock })
  const demo = await api.loadRunDemo!()
  const id = demo.projectData.id
  const run = (await api.runActions!.start(id, { plan_ref: demo.latestPlan!.ref, mode: 'auto' })).run!
  await expect(api.runActions!.act(id, run.id, { action: 'step', expected_revision: run.revision }))
    .rejects.toMatchObject({ code: 'INVALID_STATE' })
  const started = await api.runReads!.getRun(id, run.id)
  expect(started.tasks[0].state).toBe('running')
  await api.runActions!.act(id, run.id, { action: 'pause', expected_revision: started.revision })
  clock = 700
  const paused = await api.runReads!.getRun(id, run.id)
  expect(paused.tasks.map(({ state }) => state)).toEqual(['done', 'input_required', 'ready', 'waiting'])
  await api.runActions!.act(id, run.id, { action: 'resume', expected_revision: paused.revision })
  expect((await api.runReads!.getRun(id, run.id)).tasks[2].state).toBe('running')
})
