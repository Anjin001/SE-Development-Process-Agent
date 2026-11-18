import { describe, expect, it } from 'vitest'
import { fixture } from '../mocks/scenario'
import type { ProjectSnapshot, TaskState } from '../types/v2'
import { formatDuration } from '../components/presentation'
import { mapWorkbenchSnapshot } from './mapWorkbenchSnapshot'

const { project_snapshot: project, plan_response: plan, review_snapshot: run, review_response, approval_event } = fixture

describe('v2 工作台快照映射', () => {
  it('保留计划绑定、排期、任务来源、依赖、多个阻塞原因和产物版本', () => {
    const blocked = run.tasks.find(({ task_id }) => task_id === 'T4')!
    const additionalBlocker = { code: 'UPSTREAM_RUNNING', ref_id: 'T3', message: '等待前置任务完成' }
    const snapshot = mapWorkbenchSnapshot(project, plan, {
      ...run,
      tasks: run.tasks.map((task) => task === blocked ? { ...task, blockers: [...task.blockers, additionalBlocker] } : task),
    }, [])

    expect(snapshot.activeStage).toBe('execution')
    expect(snapshot.plan).toMatchObject({
      ref: { id: 'PLAN-01', version: 1 },
      specRef: { id: 'SPEC-01', version: 1 },
      concurrency: 2,
      plannedSeconds: 1200,
    })
    expect(snapshot.plan?.tasks.find(({ id }) => id === 'T4')).toMatchObject({
      dependencies: ['T2', 'T3'],
      estimateSeconds: 300,
      onCriticalPath: true,
      requirementIds: ['REQ-01', 'REQ-02'],
      decisionIds: ['DEC-01'],
      writePaths: ['app/integration.py'],
      outputs: [{ id: 'OUT-01', kind: 'git_commit', path: 'app/integration.py' }],
      attempts: [],
      blockers: [...blocked.blockers, additionalBlocker],
    })
    expect(snapshot.plan?.tasks.find(({ id }) => id === 'T3')?.onCriticalPath).toBe(false)
    const reviewed = mapWorkbenchSnapshot(project, plan, review_response, [])
    const task = reviewed.plan?.tasks.find(({ id }) => id === 'T2')
    expect(task?.artifacts).toContainEqual(
      expect.objectContaining({ id: 'ART-T2', version: 2, based_on_version: 1, content: null }),
    )
    expect(task?.attempts).toContainEqual(expect.objectContaining({ id: 'T2.1', state: 'succeeded' }))
    expect(task?.reviewRecords[0]).toMatchObject({ decision: 'revise', approved_versions: { 'ART-T2': 2 } })
  })

  it.each<TaskState>(['waiting', 'ready', 'running', 'review', 'failed', 'input_required', 'blocked', 'done'])(
    '保留服务端任务状态 %s 和缺项列表',
    (state) => {
      const tasks = run.tasks.map((task) => task.task_id === 'T2'
        ? { ...task, state, missing_input_ids: state === 'input_required' ? ['OPEN-01'] : [] }
        : task)
      const task = mapWorkbenchSnapshot(project, plan, { ...run, tasks }, []).plan?.tasks.find(({ id }) => id === 'T2')
      expect(task?.status).toBe(state)
      expect(task?.missingInputIds).toEqual(state === 'input_required' ? ['OPEN-01'] : [])
    },
  )

  it('没有排期和运行时保留未知估时，不填造任务状态或关键路径', () => {
    const draftPlan = {
      plan: {
        ...plan.plan, base_commit: null,
        tasks: plan.plan.tasks.map((task) => ({ ...task, estimate_seconds: null, outputs: [{ id: 'OUT-01', kind: 'text' as const, path: null }] })),
      },
      schedule: null,
    }
    const snapshot = mapWorkbenchSnapshot({ ...project, stage: 'planning', runs: [] }, draftPlan, null, [])
    expect(snapshot.activeStage).toBe('workflow')
    expect(snapshot.plan?.plannedSeconds).toBeNull()
    expect(snapshot.plan?.tasks[0]).toMatchObject({
      estimateSeconds: null, onCriticalPath: false, status: 'unstarted',
      outputs: [{ id: 'OUT-01', kind: 'text', path: null }], attempts: [], artifacts: [],
    })
    expect(snapshot.run).toBeNull()
  })

  it('没有有效计划时保持空态，不混用运行绑定的其他计划版本', () => {
    const early: ProjectSnapshot = { ...project, stage: 'intake', race: null, specification: null, latest_plan_ref: null, runs: [] }
    const snapshot = mapWorkbenchSnapshot(early, null, null, [])
    expect(snapshot.activeStage).toBe('intake')
    expect(snapshot.specificationRef).toBeNull()
    expect(snapshot.plan).toBeNull()
    expect(snapshot.stages.find(({ id }) => id === 'execution')?.status).toBe('waiting')
    expect(mapWorkbenchSnapshot(project, { ...plan, plan: { ...plan.plan, version: 2 } }, run, []).plan).toBeNull()
  })

  it('仅展示快照游标之前的同运行事件，按序号去重并排序', () => {
    expect(mapWorkbenchSnapshot(project, plan, run, [approval_event]).activities).toEqual([])
    const pause = { ...approval_event, seq: 9, type: 'run.paused', task_id: null, attempt_id: null }
    const snapshot = mapWorkbenchSnapshot(project, plan, review_response, [
      approval_event, pause, approval_event, { ...pause, run_id: 'OTHER' },
    ])
    expect(snapshot.activities.map(({ id }) => id)).toEqual(['RUN-01:9', 'RUN-01:10'])
    expect(snapshot.activities[0].description).toBe('RUN-01')
  })
})

describe('秒数展示', () => {
  it.each([[null, '未估时'], [0, '0 秒'], [45, '45 秒'], [90, '1 分 30 秒'], [600, '10 分钟']] as const)(
    '保留 %s 秒的精度',
    (seconds, expected) => expect(formatDuration(seconds)).toBe(expected),
  )
})
