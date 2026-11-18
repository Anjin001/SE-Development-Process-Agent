import type { GroupId, PlanResponse, ProjectSnapshot, Requirement, Specification, Task, VersionRef } from '../types/v2'
import { fixture } from '../mocks/scenario'
import { replayRun } from '../mocks/runReplay'
import { mapWorkbenchSnapshot } from './mapWorkbenchSnapshot'
import { currentRace, currentSpecification, groupIds, raceIsComplete, sameVersion } from './projectVersions'
import type { WorkbenchApi } from './workbenchApi'
import { WorkbenchApiError } from './workbenchApi'

interface MockProject {
  snapshot: ProjectSnapshot
  pending: Map<GroupId, { due: number; error: { code: string; message: string } | null }>
  startedGroups: Set<GroupId>
  plans: Map<number, PlanResponse>
  runtime: ReturnType<typeof replayRun> | null
}

const initial = fixture.project_snapshot
const sample = initial.requirement
const versionConflict = () => new WorkbenchApiError('版本已更新，请核对最新快照。', 'VERSION_CONFLICT', 409)
const invalidState = (message: string) => new WorkbenchApiError(message, 'INVALID_STATE', 409)

const normalize = (markdown: string) => markdown.replaceAll('\r\n', '\n').trimEnd()
const isSample = (markdown: string) => normalize(markdown) === normalize(sample.markdown)

function requirement(markdown: string, version: number): Requirement {
  if (markdown.trim() === '') throw new WorkbenchApiError('请填写 Markdown 需求。', 'INVALID_REQUEST', 400)
  return { id: sample.id, version, markdown, items: isSample(markdown) ? sample.items : [] }
}

// Match the recorded request only. Real cycle checks belong to the backend.
function matchesCycleExample(tasks: readonly Task[]) {
  const example = fixture.errors.plan_cycle.request.body.tasks
  return tasks.length === example.length && example.every((expected) => {
    const task = tasks.find(({ id }) => id === expected.id)
    return task?.depends_on.length === expected.depends_on.length &&
      expected.depends_on.every((id) => task.depends_on.includes(id))
  })
}

export function createMockWorkbenchApi({ now = Date.now, failFirstB = true } = {}): WorkbenchApi {
  const projectEntry = (snapshot: ProjectSnapshot): MockProject => ({
    snapshot, pending: new Map(), startedGroups: new Set(),
    plans: snapshot.latest_plan_ref ? new Map([[fixture.plan_response.plan.version, fixture.plan_response]]) : new Map(),
    runtime: snapshot.id === initial.id ? replayRun(now) : null,
  })
  const projects = new Map([[initial.id, projectEntry(initial)]])
  let nextProject = 1

  function getProject(id: string) {
    const project = projects.get(id)
    if (!project) throw new WorkbenchApiError('找不到项目。', 'NOT_FOUND', 404)
    return project
  }

  function display(entry: MockProject) {
    const project = entry.snapshot
    const runtime = project.stage === 'execution' || project.stage === 'completed' ? entry.runtime : null
    const run = runtime?.peek() ?? null
    const readPlan = (ref: VersionRef | null) => {
      const response = ref ? entry.plans.get(ref.version) : undefined
      return response && sameVersion(response.plan, ref) ? response : null
    }
    return mapWorkbenchSnapshot(project, readPlan(run?.plan_ref ?? project.latest_plan_ref), run, runtime?.history() ?? [], readPlan(project.latest_plan_ref))
  }

  function update(entry: MockProject, project: ProjectSnapshot) {
    entry.snapshot = project
    return display(entry)
  }

  function getRuntime(id: string, runId: string) {
    const runtime = getProject(id).runtime
    if (!runtime || runtime.peek().id !== runId) throw new WorkbenchApiError('找不到运行。', 'NOT_FOUND', 404)
    return runtime
  }

  function settleRace(entry: MockProject) {
    const project = entry.snapshot
    const race = currentRace(project)
    if (!race || entry.pending.size === 0) return
    const drafts = [...project.drafts]
    const evaluations = [...project.evaluations]
    const groups = race.groups.map((group) => {
      const job = entry.pending.get(group.group_id)
      if (!job || job.due > now()) return group
      entry.pending.delete(group.group_id)
      if (job.error) return { ...group, state: 'failed' as const, error: job.error }
      const draft = initial.drafts.find(({ id }) => id === group.group_id)!
      const evaluation = initial.evaluations.find(({ draft_id }) => draft_id === group.group_id)!
      drafts.push({ ...draft, requirement_ref: race.requirement_ref })
      evaluations.push(evaluation)
      return { ...group, state: 'completed' as const, error: null }
    })
    const result: ProjectSnapshot = { ...project, race: { ...race, groups }, drafts, evaluations }
    entry.snapshot = raceIsComplete(result) ? { ...result, stage: 'specification' } : result
  }

  return {
    exampleMarkdown: sample.markdown,
    async getSnapshot(id = initial.id) {
      const entry = getProject(id)
      settleRace(entry)
      if (entry.snapshot.stage === 'execution') entry.runtime?.read()
      return display(entry)
    },
    async loadRunDemo() {
      const project: ProjectSnapshot = { ...initial, id: `RUN-DEMO-${nextProject++}`, stage: 'planning', runs: [] }
      const entry = projectEntry(project)
      projects.set(project.id, entry)
      return display(entry)
    },
    runReads: {
      async getRun(id, runId) { return getRuntime(id, runId).read() },
      async getEvents(id, runId, after) { return getRuntime(id, runId).events(after) },
    },
    runActions: {
      async start(id, body) {
        const entry = getProject(id)
        if (entry.runtime?.peek().state === 'active') throw invalidState('项目已有活动运行。')
        const snapshot = entry.snapshot
        if (!sameVersion(body.plan_ref, snapshot.latest_plan_ref)) throw versionConflict()
        const plan = entry.plans.get(body.plan_ref.version)
        const spec = currentSpecification(snapshot)
        if (!plan || spec?.status !== 'confirmed' || !sameVersion(plan.plan.spec_ref, spec)) {
          throw invalidState('计划必须绑定当前已确认的规格。')
        }
        if (!sameVersion(body.plan_ref, fixture.plan_response.plan)) {
          throw invalidState('Mock 仅回放团队原版计划。请载入独立的执行控制演示。')
        }
        entry.runtime = replayRun(now, body)
        const { id: runId, plan_ref, state, started_at, ended_at } = entry.runtime.peek()
        return update(entry, { ...snapshot, stage: 'execution', runs: [
          ...snapshot.runs, { id: runId, plan_ref, state, started_at, ended_at },
        ] })
      },
      async act(id, runId, body) {
        getRuntime(id, runId).act(body)
        return display(getProject(id))
      },
    },
    async savePlan(id, planId, body) {
      const entry = getProject(id)
      const project = entry.snapshot
      const ref = project.latest_plan_ref
      if (!sameVersion({ id: planId, version: body.expected_version }, ref)) throw versionConflict()
      const previous = entry.plans.get(body.expected_version)
      if (!previous) throw new WorkbenchApiError('找不到计划版本。', 'NOT_FOUND', 404)
      if (body.tasks && matchesCycleExample(body.tasks)) {
        const { body: { error }, status } = fixture.errors.plan_cycle
        throw new WorkbenchApiError(error.message, error.code, status, error.details)
      }
      const plan = {
        ...previous.plan,
        version: previous.plan.version + 1,
        max_parallel: body.max_parallel ?? previous.plan.max_parallel,
        tasks: body.tasks ?? previous.plan.tasks,
      }
      entry.plans.set(plan.version, { plan, schedule: null })
      return update(entry, { ...project, latest_plan_ref: { id: plan.id, version: plan.version } })
    },
    projectActions: {
      async createProject(markdown) {
        const project: ProjectSnapshot = {
          id: `PROJECT-MOCK-${nextProject++}`,
          stage: 'intake',
          requirement: requirement(markdown, 1),
          race: null, drafts: [], evaluations: [], specification: null, latest_plan_ref: null, runs: [], findings: [],
        }
        const entry = projectEntry(project)
        projects.set(project.id, entry)
        return display(entry)
      },
      async saveRequirement(id, body) {
        const entry = getProject(id)
        const project = entry.snapshot
        if (project.requirement.version !== body.expected_version) throw versionConflict()
        const saved = requirement(body.markdown, project.requirement.version + 1)
        entry.pending.clear()
        entry.startedGroups.clear()
        // Keep the previous specification ref as the version base. It is not current for this requirement.
        return update(entry, {
          ...project, stage: 'intake', requirement: saved,
          race: null, drafts: [], evaluations: [], latest_plan_ref: null, findings: [],
        })
      },
      async startRace(id, requirementVersion) {
        const entry = getProject(id)
        const project = entry.snapshot
        if (project.requirement.version !== requirementVersion) throw versionConflict()
        const requirement_ref = { id: project.requirement.id, version: requirementVersion }
        const previous = currentRace(project)
        const groups = groupIds.map((group_id) => {
          const group = previous?.groups.find((item) => item.group_id === group_id)
          if (group?.state === 'completed' || group?.state === 'running') return group
          const first = !entry.startedGroups.has(group_id)
          entry.startedGroups.add(group_id)
          const error = !isSample(project.requirement.markdown)
            ? { code: 'MOCK_SAMPLE_ONLY', message: '模拟模式仅提供团队样例的出案结果。请在需求录入载入样例后提交。' }
            : group_id === 'B' && first && failFirstB
              ? { code: 'MOCK_TEMPORARY_FAILURE', message: 'B 组首次出案模拟失败，可以重试。' }
              : null
          entry.pending.set(group_id, { due: now() + (group_id === 'A' ? 500 : 1700), error })
          return { group_id, state: 'running' as const, error: null }
        })
        if (entry.pending.size === 0) return display(entry)
        return update(entry, { ...project, stage: 'race', race: { requirement_ref, groups } })
      },
      async saveSpecification(id, body) {
        const entry = getProject(id)
        const project = entry.snapshot
        const previous = project.specification
        if (body.requirement_version !== project.requirement.version || body.expected_version !== (previous?.version ?? 0)) {
          throw versionConflict()
        }
        if (!raceIsComplete(project)) throw invalidState('两组方案与评分全部就绪后才能生成规格。')
        const baseline = initial.specification!
        let content: Pick<Specification, 'markdown' | 'decisions' | 'open_items'>
        if (body.markdown !== undefined) {
          if (currentSpecification(project)?.status !== 'draft' || previous?.selection !== body.selection) throw versionConflict()
          if (body.markdown.trim() === '') throw new WorkbenchApiError('请填写规格 Markdown。', 'INVALID_REQUEST', 400)
          content = { markdown: body.markdown, decisions: body.decisions, open_items: body.open_items }
        } else if (body.selection === 'merge') {
          content = { markdown: baseline.markdown, decisions: baseline.decisions, open_items: baseline.open_items }
        } else {
          const draft = project.drafts.find(({ id: draftId }) => draftId === body.selection)!
          content = { markdown: `# ${draft.title}\n\n${draft.summary}`, decisions: [], open_items: [] }
        }
        const specification: Specification = {
          ...content, id: previous?.id ?? baseline.id, version: (previous?.version ?? 0) + 1,
          requirement_ref: { id: project.requirement.id, version: project.requirement.version },
          status: 'draft', selection: body.selection, confirmed_by: null, confirmed_at: null,
        }
        return update(entry, { ...project, stage: 'specification', specification, latest_plan_ref: null })
      },
      async confirmSpecification(id, ref) {
        const entry = getProject(id)
        const project = entry.snapshot
        const specification = currentSpecification(project)
        if (!specification || specification.id !== ref.id || specification.version !== ref.version) throw versionConflict()
        if (specification.status !== 'draft') throw invalidState('此版本已确认。')
        return update(entry, {
          ...project, stage: 'planning',
          specification: { ...specification, status: 'confirmed', confirmed_by: baselineActor, confirmed_at: new Date(now()).toISOString() },
        })
      },
    },
  }
}

const baselineActor = initial.specification?.confirmed_by ?? 'mock'
export const mockWorkbenchApi = createMockWorkbenchApi()
