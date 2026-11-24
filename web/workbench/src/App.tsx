import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { mockWorkbenchApi } from './api/mockWorkbenchApi'
import type { WorkbenchApi } from './api/workbenchApi'
import { WorkbenchApiError } from './api/workbenchApi'
import { describeConflict, describeError } from './api/workbenchFeedback'
import { startPolling } from './api/polling'
import { applyRunUpdate, isRunCurrent, mapWorkbenchSnapshot } from './api/mapWorkbenchSnapshot'
import { currentRace } from './api/projectVersions'
import { ActivityLog } from './components/ActivityLog'
import { StageContent } from './components/StageContent'
import { StageSidebar } from './components/StageSidebar'
import { WorkbenchHeader } from './components/WorkbenchHeader'
import { ProjectStages } from './components/ProjectStages'
import { ProjectDrafts, useProjectDraft } from './components/projectDrafts'
import type { PlanDraft } from './components/planDraft'
import { useRunMonitoring } from './components/useRunMonitoring'
import type { StageId, WorkbenchSnapshot } from './types/workbench'

type SnapshotState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; snapshot: WorkbenchSnapshot }

const selectionKey = (mode: string, projectId: string) => `workbench.task:${mode}:${projectId}`

function mergeSnapshot(current: SnapshotState, updated: WorkbenchSnapshot): SnapshotState {
  const previous = current.status === 'ready' ? current.snapshot : null
  if (!previous || previous.projectData.id !== updated.projectData.id) return { status: 'ready', snapshot: updated }
  const oldData = previous.projectData
  const newData = updated.projectData
  if (oldData.requirement.id !== newData.requirement.id || oldData.requirement.version > newData.requirement.version) return current
  const olderVersion = (oldRef: { id: string; version: number } | null, newRef: { id: string; version: number } | null) =>
    oldRef && newRef && oldRef.id === newRef.id && oldRef.version > newRef.version
  if (olderVersion(oldData.specification, newData.specification)
      || olderVersion(oldData.latest_plan_ref, newData.latest_plan_ref)) return current
  const olderStage = updated.stages.findIndex(({ id }) => id === updated.activeStage)
    < previous.stages.findIndex(({ id }) => id === previous.activeStage)
  const newerVersion = olderVersion(newData.specification, oldData.specification)
    || olderVersion(newData.latest_plan_ref, oldData.latest_plan_ref)
  if (olderStage && !newerVersion && newData.requirement.version === oldData.requirement.version) return current
  if (oldData.requirement.version === newData.requirement.version) {
    const oldRace = currentRace(oldData)
    const newRace = currentRace(newData)
    if (oldRace?.groups.some(({ group_id, state }) => state === 'completed'
        && !newRace?.groups.some((group) => group.group_id === group_id && group.state === 'completed'))) return current
    const oldSpec = oldData.specification
    const newSpec = newData.specification
    if (oldSpec?.status === 'confirmed' && newSpec?.status === 'draft'
        && oldSpec.id === newSpec.id && oldSpec.version === newSpec.version) return current
  }
  const previousRun = previous.run
  if (!previousRun) return { status: 'ready', snapshot: updated }
  if (previousRun.id === updated.run?.id) {
    if (!isRunCurrent(previousRun, updated.run)) {
      return { status: 'ready', snapshot: applyRunUpdate(previous, updated.run, updated.runEvents) }
    }
    const run = updated.run.revision > previousRun.revision ? updated.run : previousRun
    const project = run.state === 'completed' && newData.stage === 'execution'
      ? { ...newData, stage: 'completed' as const } : newData
    return { status: 'ready', snapshot: mapWorkbenchSnapshot(project, updated.plan?.source ?? null, run,
      [...updated.runEvents, ...previous.runEvents], updated.latestPlan?.source ?? null) }
  }
  const recorded = newData.runs.some(({ id }) => id === previousRun.id)
  if (!recorded || (updated.run && previousRun.started_at && updated.run.started_at
      && updated.run.started_at < previousRun.started_at)) return current
  if (!updated.run && oldData.requirement.version === newData.requirement.version) return current
  return { status: 'ready', snapshot: updated }
}

function rememberedTask(mode: string, projectId: string) {
  try { return sessionStorage.getItem(selectionKey(mode, projectId)) }
  catch { return null }
}

interface AppProps {
  api?: WorkbenchApi
  projectId?: string
  monitorInterval?: number
}

export default function App({ api = mockWorkbenchApi, projectId, monitorInterval = 1000 }: AppProps) {
  const [session, setSession] = useState(() => ({ api, epoch: 0, drafts: new Map<string, unknown>() }))
  if (session.api !== api) {
    setSession({ api, epoch: session.epoch + 1, drafts: new Map() })
    return null
  }
  return <ProjectSession key={JSON.stringify([session.epoch, projectId])}
    api={api} requestedProject={projectId} monitorInterval={monitorInterval} drafts={session.drafts} />
}

function requireProject(snapshot: WorkbenchSnapshot, projectId: string | undefined) {
  if (projectId !== undefined && snapshot.projectData.id !== projectId) {
    throw new WorkbenchApiError('响应与当前项目不一致。', 'INVALID_RESPONSE', 0)
  }
  return snapshot
}

function ProjectSession({ api, requestedProject, monitorInterval, drafts }: {
  api: WorkbenchApi; requestedProject?: string; monitorInterval: number; drafts: Map<string, unknown>
}) {
  const mode = api.exampleMarkdown === undefined ? 'v2' : 'mock'
  const [state, setState] = useState<SnapshotState>({ status: 'loading' })
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [viewedStage, setViewedStage] = useState<StageId>()
  const [selection, setSelection] = useState<{ projectId: string; taskId: string }>()
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [pollError, setPollError] = useState(false)
  const writing = useRef(false)
  const requestGeneration = useRef(0)
  const mounted = useRef(false)
  useLayoutEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const draftScope = state.status === 'ready' ? { projectId: state.snapshot.projectData.id, values: drafts } : undefined
  const [planDraft, setPlanDraft] = useProjectDraft<PlanDraft | null>('plan', null, draftScope)

  useEffect(() => {
    let ignore = false
    api.getSnapshot(requestedProject).then((snapshot) => requireProject(snapshot, requestedProject)).then(
      (snapshot) => {
        if (ignore) return
        setState({ status: 'ready', snapshot })
        const projectId = snapshot.projectData.id
        const taskId = rememberedTask(mode, projectId)
        if (taskId) setSelection({ projectId, taskId })
      },
      (error: unknown) => { if (!ignore) setState({ status: 'error', message: describeError(error) }) },
    )
    return () => { ignore = true }
  }, [api, loadAttempt, mode, requestedProject])

  const project = state.status === 'ready' ? state.snapshot.projectData : null
  const race = project && currentRace(project)
  const runningProjectId = race?.groups.some(({ state }) => state === 'running' || state === 'pending') ? project?.id : undefined

  useEffect(() => {
    if (!runningProjectId) return
    return startPolling({
      interval: 750,
      async poll(_recovering, isActive) {
        if (writing.current) return false
        const generation = requestGeneration.current
        const snapshot = requireProject(await api.getSnapshot(runningProjectId), runningProjectId)
        if (!isActive() || generation !== requestGeneration.current) return false
        setState((current) => current.status === 'ready' && current.snapshot.projectData.id === runningProjectId
          ? mergeSnapshot(current, snapshot) : current)
        setPollError(false)
      },
      onError() { setPollError(true) },
    })
  }, [api, runningProjectId])

  const sync = useRunMonitoring(
    state.status === 'ready' ? state.snapshot : null, api, writing, requestGeneration,
    (run, events, projectId) => setState((current) => current.status === 'ready' && current.snapshot.projectData.id === projectId
      ? { status: 'ready', snapshot: applyRunUpdate(current.snapshot, run, events) } : current),
    (snapshot, projectId) => setState((current) => current.status === 'ready' && current.snapshot.projectData.id === projectId
      ? mergeSnapshot(current, snapshot) : current),
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
  const selectedTaskId = selection?.projectId === projectData.id && snapshot.plan?.tasks.some(({ id }) => id === selection.taskId) ? selection.taskId
    : snapshot.plan?.tasks.find(({ blockers }) => blockers.length > 0)?.id ?? snapshot.plan?.tasks[0]?.id
  const savePlan = api.savePlan
  const { runActions, loadRunDemo } = api
  const runId = snapshot.run?.id

  function selectTask(taskId: string) {
    setSelection({ projectId: projectData.id, taskId })
    try { sessionStorage.setItem(selectionKey(mode, projectData.id), taskId) }
    catch { /* Keep the in-memory selection when browser storage is unavailable. */ }
  }

  function acceptSnapshot(updated: WorkbenchSnapshot) {
    setState((current) => mergeSnapshot(current, updated))
  }

  async function perform(request: () => Promise<WorkbenchSnapshot>, opensProject = false) {
    if (writing.current) return false
    writing.current = true
    requestGeneration.current += 1
    setBusy(true)
    setNotice('')
    try {
      const updated = requireProject(await request(), opensProject ? undefined : projectData.id)
      if (!mounted.current) return false
      if (updated.projectData.id !== projectData.id) {
        setViewedStage(undefined)
        const taskId = rememberedTask(mode, updated.projectData.id)
        setSelection(taskId ? { projectId: updated.projectData.id, taskId } : undefined)
      }
      acceptSnapshot(updated)
      setPollError(false)
      return true
    } catch (error) {
      if (!mounted.current) return false
      if (error instanceof WorkbenchApiError && error.status === 409) {
        const conflict = error.code === 'VERSION_CONFLICT' ? '版本已更新。' : describeError(error)
        try {
          const updated = requireProject(await api.getSnapshot(projectData.id), projectData.id)
          if (!mounted.current) return false
          acceptSnapshot(updated)
          setNotice(`${conflict}已载入最新快照，请核对后再操作。 ${describeConflict(snapshot, updated)}`)
        } catch (refreshError) {
          if (!mounted.current) return false
          setNotice(`${conflict}未能读取最新快照。 ${describeError(refreshError)}`)
        }
      } else {
        setNotice(describeError(error))
      }
      return false
    } finally {
      writing.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <ProjectDrafts.Provider value={draftScope ?? null}>
    <div className="workbench">
      <WorkbenchHeader snapshot={snapshot} viewedStage={stage.id} demo={api.exampleMarkdown !== undefined}
        onStageChange={(id) => { setViewedStage(id); setNotice('') }} />
      <div className="workspace">
        <StageContent key={JSON.stringify([projectData.id, stage.id])} stage={stage}
          plan={stage.id === 'workflow' ? snapshot.latestPlan : snapshot.plan}
          latestPlan={snapshot.latestPlan}
          run={snapshot.run}
          selectedId={selectedTaskId}
          submitReview={runActions && runId ? (command) => perform(() => runActions.act(projectData.id, runId, command)) : undefined}
          planEditor={{
            draft: planDraft,
            onDraftChange: setPlanDraft,
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
              allowCreate={requestedProject === undefined}
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
          loadDemo: loadRunDemo && requestedProject === undefined ? () => { void perform(loadRunDemo, true).then((ok) => { if (ok) setViewedStage('workflow') }) } : undefined,
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
    </ProjectDrafts.Provider>
  )
}
