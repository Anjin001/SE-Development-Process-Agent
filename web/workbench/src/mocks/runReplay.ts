import { fixture } from './scenario'
import { pendingArtifacts, validCommit } from '../api/reviewArtifacts'
import { WorkbenchApiError } from '../api/workbenchApi'
import type { Attempt, ReviewRecord, Run, RunCommand, RunEvent, RunTask, StartRunRequest } from '../types/v2'

// This replay is tied to the four team fixture tasks. It has no plan traversal,
// scheduling rules, worker access, or execution of project code.
const sample = fixture.review_snapshot
const taskRecord = (id: string) => sample.tasks.find(({ task_id }) => task_id === id)!
const missingId = 'OPEN-01'

function invalid(message: string) {
  return new WorkbenchApiError(message, 'INVALID_STATE', 409)
}

export function replayRun(now: () => number, start?: StartRunRequest) {
  const at = () => new Date(now()).toISOString()
  let run: Run = start ? {
    ...sample, id: 'RUN-MOCK-01', plan_ref: start.plan_ref, mode: start.mode, revision: 0, latest_event_seq: 0,
    started_at: at(), ended_at: null, findings: [],
    tasks: sample.tasks.map(({ task_id }) => ({
      task_id, state: task_id === 'T1' ? 'ready' : 'waiting',
      attempts: [], artifacts: [], blockers: [], missing_input_ids: [], review_records: [],
    })),
  } : sample
  const events: RunEvent[] = []
  const reports: { due: number; publish: () => void }[] = []
  const task = (id: string) => run.tasks.find((item) => item.task_id === id)!

  function commit(type: string, patch: Partial<Run>, taskId: string | null = null, attemptId: string | null = null) {
    run = { ...run, ...patch, revision: run.revision + 1, latest_event_seq: run.latest_event_seq + 1 }
    events.push({ seq: run.latest_event_seq, type, run_id: run.id, task_id: taskId, attempt_id: attemptId, created_at: at(), payload: {} })
  }

  function replaceTasks(changes: Readonly<Record<string, Partial<RunTask>>>) {
    return run.tasks.map((item) => changes[item.task_id] ? { ...item, ...changes[item.task_id] } : item)
  }

  function dispatch(id: string, delay: number, report: (attempt: Attempt) => void) {
    const previous = task(id).attempts
    const attempt: Attempt = {
      id: `${id}.${previous.length + 1}`, number: previous.length + 1, state: 'running',
      created_at: at(), started_at: at(), ended_at: null, error: null, input_refs: [],
    }
    commit('task.dispatched', { tasks: replaceTasks({ [id]: { state: 'running', attempts: [...previous, attempt] } }) }, id, attempt.id)
    reports.push({ due: now() + delay, publish: () => report(attempt) })
  }

  function report(id: string, attempt: Attempt, state: Attempt['state'], event: string,
    change: Partial<RunTask>, others: Readonly<Record<string, Partial<RunTask>>> = {}, error: Attempt['error'] = null,
    findings = run.findings) {
    const attempts = task(id).attempts.map((item) => item.id === attempt.id ? { ...item, state, ended_at: at(), error } : item)
    commit(event, { tasks: replaceTasks({ ...others, [id]: { ...change, attempts } }), findings }, id, attempt.id)
  }

  function playBatch() {
    if (!start) return false
    if (task('T1').state === 'ready') {
      dispatch('T1', 500, (attempt) => {
        report('T1', attempt, 'succeeded', 'task.completed', { state: 'done', artifacts: taskRecord('T1').artifacts },
          { T2: { state: 'input_required', missing_input_ids: [missingId] }, T3: { state: 'ready' } })
        commit('task.input_required', { findings: [{
          stage: 'execution', code: 'MOCK_INPUT_REQUIRED', severity: 'warning',
          summary: '演示：预约限制任务缺少设备编号。', evidence_refs: [missingId, run.plan_ref],
          affected_task_ids: ['T2'], suggested_action: '选择 T2，仅补充当前缺少的输入。',
        }] }, 'T2')
      })
      return true
    }
    // Fixed replay branches, allowing T3 to be demonstrated independently of T2.
    const playT2 = task('T2').state === 'ready'
    const playT3 = task('T3').state === 'ready'
    if (playT2) {
      dispatch('T2', 450, (attempt) => {
        if (attempt.number === 1) {
          const error = { code: 'EXECUTION_FAILED', message: '演示：预约限制任务的首次尝试失败。', retryable: true, details: {} }
          report('T2', attempt, 'failed', 'task.failed', { state: 'failed' }, { T4: {
            state: 'blocked', blockers: [{ code: 'UPSTREAM_FAILED', ref_id: 'T2', message: '上游 T2 失败，集成任务受阻。' }],
          } }, error, [{
            stage: 'execution', code: 'MOCK_UPSTREAM_FAILED', severity: 'error',
            summary: '演示：失败阻塞了集成任务，独立任务仍可回报。', evidence_refs: [attempt.id, run.plan_ref],
            affected_task_ids: ['T2', 'T4'], suggested_action: '查看失败尝试后，可重试 T2。',
          }])
        } else {
          report('T2', attempt, 'succeeded', 'artifact.submitted', {
            state: 'review', artifacts: [...task('T2').artifacts, ...taskRecord('T2').artifacts.map((item) => ({
              ...item, attempt_id: attempt.id,
              version: Math.max(0, ...task('T2').artifacts.filter(({ id }) => id === item.id).map(({ version }) => version)) + 1,
            }))],
          }, { T4: { state: 'waiting', blockers: taskRecord('T4').blockers } })
        }
      })
    }
    if (playT3) {
      dispatch('T3', 1100, (attempt) => report('T3', attempt, 'succeeded', 'task.completed',
        { state: 'done', artifacts: taskRecord('T3').artifacts }))
    }
    return playT2 || playT3
  }

  function read() {
    reports.sort((a, b) => a.due - b.due)
    while (reports[0] && reports[0].due <= now()) reports.shift()!.publish()
    if (run.mode === 'auto' && run.dispatch === 'active' && reports.length === 0) playBatch()
    return run
  }

  if (start) commit('run.started', {})
  return {
    peek: () => run,
    read,
    events(after: number) {
      read()
      const remaining = events.filter(({ seq }) => seq > after)
      const items = remaining.slice(0, 100)
      return { items, next_after: items.at(-1)?.seq ?? after, has_more: remaining.length > items.length }
    },
    history: () => events,
    act(command: RunCommand) {
      if (command.expected_revision !== run.revision) {
        throw new WorkbenchApiError('运行修订号已变化。', 'VERSION_CONFLICT', 409, { current_revision: run.revision })
      }
      if (run.state !== 'active') throw invalid('运行已结束。')
      switch (command.action) {
        case 'pause':
          if (run.dispatch !== 'active') throw invalid('派发已经暂停。')
          commit('run.paused', { dispatch: 'paused' })
          break
        case 'resume':
          if (run.dispatch !== 'paused') throw invalid('派发未暂停。')
          commit('run.resumed', { dispatch: 'active' })
          break
        case 'step':
          if (run.mode !== 'manual' || run.dispatch !== 'active' || reports.length > 0 || !playBatch()) {
            throw invalid('当前演示运行无法接受单步操作。')
          }
          break
        case 'retry': {
          if (!start || command.task_id !== 'T2' || task('T2').state !== 'failed') throw invalid('只有失败任务可以重试。')
          commit('task.retry_requested', {
            tasks: replaceTasks({
              T2: { state: 'waiting' },
              T4: { state: 'waiting', blockers: [{ code: 'UPSTREAM_PENDING', ref_id: 'T2', message: '等待 T2 重试。' }] },
            }), findings: [],
          }, 'T2')
          reports.push({ due: now() + 200, publish: () => commit('task.ready', { tasks: replaceTasks({ T2: { state: 'ready' } }) }, 'T2') })
          break
        }
        case 'supply-input': {
          if (!start || command.task_id !== 'T2' || task('T2').state !== 'input_required') throw invalid('任务当前无需补充输入。')
          const fields = Object.keys(command.values)
          if (fields.length !== 1 || fields[0] !== missingId || !command.values[missingId].trim()) {
            throw new WorkbenchApiError('只能提交当前缺项的非空文本。', 'INVALID_REFERENCE', 422)
          }
          commit('task.input_supplied', { tasks: replaceTasks({ T2: { state: 'ready', missing_input_ids: [] } }), findings: [] }, 'T2')
          break
        }
        case 'review': {
          // Replay the fixture's T2/T4 review example only.
          if (command.task_id !== 'T2' || task('T2').state !== 'review') throw invalid('任务不在待审核状态。')
          const current = task('T2')
          const pending = pendingArtifacts(current.artifacts)
          if (!pending.length || Object.keys(command.expected_versions).length !== pending.length ||
              pending.some(({ id, version }) => command.expected_versions[id] !== version)) {
            throw new WorkbenchApiError('待审核版本已变化。', 'VERSION_CONFLICT', 409)
          }
          const changes = Object.entries(command.revisions)
          const invalidChanges = changes.some(([id, value]) => {
            const item = pending.find((artifact) => artifact.id === id)
            return !item || (item.kind === 'text'
              ? !('content' in value) || !value.content.trim()
              : !('commit_sha' in value) || !validCommit(value.commit_sha))
          })
          if (invalidChanges || (command.decision === 'revise' ? !changes.length : changes.length > 0) ||
              (command.decision === 'reject' && !command.note.trim())) {
            throw new WorkbenchApiError('审核内容无效。', 'INVALID_REQUEST', 422)
          }
          const rejected = command.decision === 'reject'
          const approved: [string, number][] = []
          const artifacts = current.artifacts.flatMap((item) => {
            if (!pending.includes(item)) return [item]
            if (rejected) return [{ ...item, status: 'rejected' as const }]
            const revision = command.revisions[item.id]
            if (!revision) {
              approved.push([item.id, item.version])
              return [{ ...item, status: 'approved' as const }]
            }
            const version = item.version + 1
            approved.push([item.id, version])
            return [item, { ...item, ...revision, version, based_on_version: item.version, origin: 'human' as const, status: 'approved' as const }]
          })
          const record: ReviewRecord = {
            task_id: 'T2', decision: command.decision, expected_versions: command.expected_versions,
            approved_versions: Object.fromEntries(approved), actor_id: 'mock-reviewer', note: command.note, created_at: at(),
          }
          const downstream = fixture.review_response.tasks.find(({ task_id }) => task_id === 'T4')!
          commit(rejected ? 'artifact.rejected' : 'artifact.approved', {
            tasks: replaceTasks({
              T2: { state: rejected ? 'failed' : 'done', artifacts, review_records: [...current.review_records, record] },
              T4: rejected ? { state: 'blocked', blockers: [{ code: 'UPSTREAM_FAILED', ref_id: 'T2', message: 'T2 被退回，集成任务受阻。' }] }
                : { state: downstream.state, blockers: downstream.blockers },
            }),
          }, 'T2', current.attempts.at(-1)?.id)
          break
        }
      }
    },
  }
}
