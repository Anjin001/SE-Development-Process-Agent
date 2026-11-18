import { useEffect, useRef, useState } from 'react'
import { mockWorkbenchApi } from './api/mockWorkbenchApi'
import type { WorkbenchApi } from './api/workbenchApi'
import { WorkbenchApiError } from './api/workbenchApi'
import { describeConflict, describeError } from './api/workbenchFeedback'
import { applyRunUpdate } from './api/mapWorkbenchSnapshot'
import { currentRace } from './api/projectVersions'
import { ActivityLog } from './components/ActivityLog'
import { StageContent } from './components/StageContent'
import { StageSidebar } from './components/StageSidebar'
import { WorkbenchHeader } from './components/WorkbenchHeader'
import { ProjectStages } from './components/ProjectStages'
import type { PlanDraft } from './components/planDraft'
import { useRunMonitoring } from './components/useRunMonitoring'
import type { StageId, WorkbenchSnapshot } from './types/workbench'

type SnapshotState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; snapshot: WorkbenchSnapshot }

const selectionKey = (projectId: string) => `workbench.task:${projectId}`

function mergeSnapshot(current: SnapshotState, updated: WorkbenchSnapshot): SnapshotState {
  const previous = current.status === 'ready' ? current.snapshot : null
  const previousRun = previous?.run
  const sameRun = previousRun && previous.projectData.id === updated.projectData.id && previousRun.id === updated.run?.id
  return { status: 'ready', snapshot: sameRun ? applyRunUpdate(updated, previousRun, previous.runEvents) : updated }
}

function rememberedTask(projectId: string) {
  try { return sessionStorage.getItem(selectionKey(projectId)) }
  catch { return null }
}

export default function App({ api = mockWorkbenchApi, monitorInterval = 1000 }: { api?: WorkbenchApi; monitorInterval?: number }) {
  const [state, setState] = useState<SnapshotState>({ status: 'loading' })
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [viewedStage, setViewedStage] = useState<StageId>()
  const [selection, setSelection] = useState<{ projectId: string; taskId: string }>()
  const [planDraft, setPlanDraft] = useState<{ projectId: string; draft: PlanDraft }>()
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [pollError, setPollError] = useState(false)
  const writing = useRef(false)
  const requestGeneration = useRef(0)

  useEffect(() => {
    let ignore = false
    api.getSnapshot().then(
      (snapshot) => {
        if (ignore) return
        setState({ status: 'ready', snapshot })
        const projectId = snapshot.projectData.id
        const taskId = rememberedTask(projectId)
        if (taskId) setSelection({ projectId, taskId })
      },
      (error: unknown) => { if (!ignore) setState({ status: 'error', message: describeError(error) }) },
    )
    return () => { ignore = true }
  }, [api, loadAttempt])

  const project = state.status === 'ready' ? state.snapshot.projectData : null
  const race = project && currentRace(project)
  const runningProjectId = race?.groups.some(({ state }) => state === 'running' || state === 'pending') ? project?.id : undefined

  useEffect(() => {
    if (!runningProjectId) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>

    async function refresh() {
      if (!writing.current) {
        const generation = requestGeneration.current
        try {
          const snapshot = await api.getSnapshot(runningProjectId)
          if (!stopped && generation === requestGeneration.current) {
            setState((current) => mergeSnapshot(current, snapshot))
            setPollError(false)
          }
        } catch {
          if (!stopped && generation === requestGeneration.current) setPollError(true)
        }
      }
      if (!stopped) timer = setTimeout(refresh, 750)
    }

    timer = setTimeout(refresh, 750)
    return () => { stopped = true; clearTimeout(timer) }
  }, [api, runningProjectId])

  const sync = useRunMonitoring(
    state.status === 'ready' ? state.snapshot : null, api.runReads, writing, requestGeneration,
    (run, events) => setState((current) => current.status === 'ready'
      ? { status: 'ready', snapshot: applyRunUpdate(current.snapshot, run, events) } : current),
    monitorInterval,
  )

  if (state.status === 'loading') return <main className="loading-state" role="status">正在载入工作台…</main>
  if (state.status === 'error') return <main className="loading-state">
    <p role="alert">无法载入工作台。{state.message}</p>
    <button type="button" className="run-button" onClick={() => {
      setState({ status: 'loading' })
      setLoadAttempt((attempt) => attempt + 1)
    }}>重新载入</button>
  </main>

  const { snapshot } = state
  const stage = snapshot.stages.find(({ id }) => id === (viewedStage ?? snapshot.activeStage))
    ?? snapshot.stages.find(({ id }) => id === snapshot.activeStage)
  if (!stage) return <main className="loading-state" role="alert">暂时没有可显示的阶段。</main>
  const { projectData } = snapshot
  const selectedTaskId = selection?.projectId === projectData.id ? selection.taskId
    : snapshot.plan?.tasks.find(({ blockers }) => blockers.length > 0)?.id ?? snapshot.plan?.tasks[0]?.id
  const savePlan = api.savePlan
  const { runActions, loadRunDemo } = api
  const runId = snapshot.run?.id

  function selectTask(taskId: string) {
    setSelection({ projectId: projectData.id, taskId })
    try { sessionStorage.setItem(selectionKey(projectData.id), taskId) }
    catch { /* Keep the in-memory selection when browser storage is unavailable. */ }
  }

  function acceptSnapshot(updated: WorkbenchSnapshot) {
    setState((current) => mergeSnapshot(current, updated))
  }

  async function perform(request: () => Promise<WorkbenchSnapshot>) {
    if (writing.current) return false
    writing.current = true
    requestGeneration.current += 1
    setBusy(true)
    setNotice('')
    try {
      const updated = await request()
      acceptSnapshot(updated)
      setPollError(false)
      return true
    } catch (error) {
      if (error instanceof WorkbenchApiError && error.status === 409) {
        const conflict = error.code === 'VERSION_CONFLICT' ? '版本已更新。' : describeError(error)
        try {
          const updated = await api.getSnapshot(projectData.id)
          acceptSnapshot(updated)
          setNotice(`${conflict}已载入最新快照，请核对后再操作。 ${describeConflict(snapshot, updated)}`)
        } catch (refreshError) {
          setNotice(`${conflict}未能读取最新快照。 ${describeError(refreshError)}`)
        }
      } else {
        setNotice(describeError(error))
      }
      return false
    } finally {
      writing.current = false
      setBusy(false)
    }
  }

  return (
    <div className="workbench">
      <WorkbenchHeader snapshot={snapshot} viewedStage={stage.id} demo={api.exampleMarkdown !== undefined}
        onStageChange={(id) => { setViewedStage(id); setNotice('') }} />
      <div className="workspace">
        <StageContent key={stage.id} stage={stage}
          plan={stage.id === 'workflow' ? snapshot.latestPlan : snapshot.plan}
          latestPlan={snapshot.latestPlan}
          run={snapshot.run}
          selectedId={selectedTaskId}
          submitReview={runActions && runId ? (command) => perform(() => runActions.act(projectData.id, runId, command)) : undefined}
          planEditor={{
            draft: planDraft?.projectId === projectData.id ? planDraft.draft : null,
            onDraftChange: (draft) => setPlanDraft(draft ? { projectId: projectData.id, draft } : undefined),
            onSave: savePlan ? (id, body) => perform(() => savePlan(projectData.id, id, body)) : undefined,
            busy,
          }}
          onSelect={selectTask}
          notice={notice || (pollError && runningProjectId ? '暂时无法刷新赛马进度，稍后将重试。' : '')}
          onRefresh={notice ? () => { void perform(() => api.getSnapshot(projectData.id)) } : undefined}>
          {(stage.id === 'intake' || stage.id === 'race' || stage.id === 'review') && (
            <ProjectStages
              key={projectData.id}
              stage={stage.id}
              project={projectData}
              actions={api.projectActions}
              exampleMarkdown={api.exampleMarkdown}
              busy={busy}
              perform={perform}
              navigate={setViewedStage}
            />
          )}
        </StageContent>
        <StageSidebar stage={stage} snapshot={snapshot} selectedId={selectedTaskId} onSelect={selectTask} controls={{
          busy, sync,
          refresh: () => { void perform(() => api.getSnapshot(projectData.id)) },
          openExecution: () => setViewedStage('execution'),
          loadDemo: loadRunDemo ? () => { void perform(loadRunDemo).then((ok) => { if (ok) setViewedStage('workflow') }) } : undefined,
          start: runActions ? async (body) => {
            const ok = await perform(() => runActions.start(projectData.id, body))
            if (ok) setViewedStage('execution')
            return ok
          } : undefined,
          act: runActions && runId ? (body) => perform(() => runActions.act(projectData.id, runId, body)) : undefined,
        }} />
      </div>
      <ActivityLog entries={snapshot.activities} />
    </div>
  )
}
