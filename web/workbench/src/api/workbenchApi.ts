import type { PlanResponse, ProjectSnapshot, Run, RunCommand, RunEventsPage, SaveRequirementRequest, SaveSpecificationRequest, StartRunRequest, UpdatePlanRequest, VersionRef } from '../types/v2'
import type { WorkbenchSnapshot } from '../types/workbench'
import { mapWorkbenchSnapshot } from './mapWorkbenchSnapshot'
import { sameVersion } from './projectVersions'
import { mapV2Events, mapV2Plan, mapV2Project, mapV2Run, mapV2Specification, mapV2UpdatedPlan, validateV2Bindings } from './v2Mapping'
import { WorkbenchApiError } from './workbenchApiError'
export { WorkbenchApiError } from './workbenchApiError'

export interface WorkbenchApi {
  getSnapshot(projectId?: string): Promise<WorkbenchSnapshot>
  projectActions?: ProjectActions
  savePlan?: (projectId: string, planId: string, body: UpdatePlanRequest) => Promise<WorkbenchSnapshot>
  runReads?: RunReads
  runActions?: RunActions
  loadRunDemo?: () => Promise<WorkbenchSnapshot>
  exampleMarkdown?: string
}

export interface RunReads {
  getRun(projectId: string, runId: string): Promise<Run>
  getEvents(projectId: string, runId: string, after: number): Promise<RunEventsPage>
}

export interface RunActions {
  start(projectId: string, body: StartRunRequest): Promise<WorkbenchSnapshot>
  act(projectId: string, runId: string, body: RunCommand): Promise<WorkbenchSnapshot>
}

export interface ProjectActions {
  createProject(markdown: string): Promise<WorkbenchSnapshot>
  saveRequirement(projectId: string, body: SaveRequirementRequest): Promise<WorkbenchSnapshot>
  startRace(projectId: string, requirementVersion: number): Promise<WorkbenchSnapshot>
  saveSpecification(projectId: string, body: SaveSpecificationRequest): Promise<WorkbenchSnapshot>
  confirmSpecification(projectId: string, specification: VersionRef): Promise<WorkbenchSnapshot>
}

const projectsPath = '/api/v2/projects'
const projectPath = (id: string) => `${projectsPath}/${encodeURIComponent(id)}`

export async function withApiDeadline<T>(operation: (signal: AbortSignal) => Promise<T>, timeout = 15000): Promise<T> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          reject(new WorkbenchApiError('请求超时，请重试。', 'TIMEOUT', 0))
          controller.abort()
        }, timeout)
      }),
      operation(controller.signal),
    ])
  } catch (error) {
    if (error instanceof TypeError) throw new WorkbenchApiError('网络连接失败，请检查连接后重试。', 'NETWORK_ERROR', 0)
    if (error instanceof SyntaxError) throw new WorkbenchApiError('接口返回了无效的 JSON。', 'INVALID_RESPONSE', 0)
    throw error
  } finally {
    clearTimeout(timer)
  }
}

async function responseError(response: Response, method: string, path: string) {
  const data = await response.json().catch(() => null) as { error?: { code?: unknown; message?: unknown; details?: unknown } } | null
  const code = typeof data?.error?.code === 'string' ? data.error.code : 'HTTP_ERROR'
  const message = typeof data?.error?.message === 'string' ? data.error.message : `${method} ${path}: HTTP ${response.status}`
  const details = data?.error?.details
  return new WorkbenchApiError(message, code, response.status,
    details && typeof details === 'object' && !Array.isArray(details) ? details as Record<string, unknown> : {})
}

export function createV2ReadApi(projectId: string, request: typeof fetch = fetch) {
  const path = projectPath(projectId)
  const runPath = (id: string) => `${path}/runs/${encodeURIComponent(id)}`

  function read<T>(path: string, decode: (value: unknown) => T): Promise<T> {
    return withApiDeadline(async (signal) => {
      const response = await request(path, { signal, headers: { Accept: 'application/json' } })
      if (!response.ok) throw await responseError(response, 'GET', path)
      return decode(await response.json())
    })
  }

  return {
    getProject: () => read(path, (value) => mapV2Project(value, projectId)),
    getPlan: (ref: VersionRef) => read(`${path}/plans/${encodeURIComponent(ref.id)}/versions/${ref.version}`, (value) => mapV2Plan(value, ref.id, ref.version)),
    getRun: (id: string) => read(runPath(id), (value) => mapV2Run(value, id, projectId)),
    getEvents: (id: string, after = 0, limit = 100) => read(`${runPath(id)}/events?after=${after}&limit=${limit}`, (value) => mapV2Events(value, id, after)),
  }
}

export function createHttpWorkbenchApi(projectId: string, request: typeof fetch = fetch): WorkbenchApi {
  const write = createV2Writer<WorkbenchSnapshot>(request)

  async function loadProject(project: ProjectSnapshot, updated?: PlanResponse, returnedRun?: Run): Promise<WorkbenchSnapshot> {
    const api = createV2ReadApi(project.id, request)
    const summary = project.stage === 'execution' || project.stage === 'completed'
      ? project.runs.find((item) => item.state === 'active') ??
        [...project.runs].sort((left, right) => (right.started_at ?? '').localeCompare(left.started_at ?? ''))[0]
      : undefined
    const run = returnedRun && summary?.id === returnedRun.id ? returnedRun : summary ? await api.getRun(summary.id) : null
    if (returnedRun && !project.runs.some((item) => item.id === returnedRun.id && sameVersion(item.plan_ref, returnedRun.plan_ref))) {
      throw new WorkbenchApiError('操作返回的运行不属于项目或绑定了不同计划版本。', 'INVALID_RESPONSE', 0)
    }
    const planRef = run?.plan_ref ?? project.latest_plan_ref
    const latestRef = project.latest_plan_ref
    const readPlan = (ref: VersionRef | null) => {
      if (!ref) return null
      return updated && sameVersion(updated.plan, ref) ? updated : api.getPlan(ref)
    }
    const [plan, events, latestPlan] = await Promise.all([
      readPlan(planRef),
      run && run.latest_event_seq > 0 ? api.getEvents(run.id, Math.max(0, run.latest_event_seq - 100)) : null,
      sameVersion(planRef, latestRef) ? null : readPlan(latestRef),
    ])
    const currentPlan = sameVersion(planRef, latestRef) ? plan : latestPlan
    validateV2Bindings(project, plan, currentPlan, run)
    return mapWorkbenchSnapshot(project, plan, run, events?.items ?? [], currentPlan)
  }

  async function getSnapshot(id = projectId) {
    return loadProject(await createV2ReadApi(id, request).getProject())
  }

  const projectResponse = (id?: string) => async (response: Response) => loadProject(mapV2Project(await response.json(), id))
  const runResponse = (id: string, runId?: string) => async (response: Response) => {
    const run = mapV2Run(await response.json(), runId, id)
    return loadProject(await createV2ReadApi(id, request).getProject(), undefined, run)
  }
  return {
    getSnapshot,
    runReads: {
      getRun: (id, runId) => createV2ReadApi(id, request).getRun(runId),
      getEvents: (id, runId, after) => createV2ReadApi(id, request).getEvents(runId, after),
    },
    runActions: {
      start: (id, body) => write(`${projectPath(id)}/runs`, body, runResponse(id)),
      act: (id, runId, { action, ...body }) => write(
        `${projectPath(id)}/runs/${encodeURIComponent(runId)}/actions/${action}`, body, runResponse(id, runId),
      ),
    },
    savePlan: (id, planId, body) => write(`${projectPath(id)}/plans/${encodeURIComponent(planId)}`, body, async (response) => {
      const updated = mapV2UpdatedPlan(await response.json(), planId, body.expected_version)
      return loadProject(await createV2ReadApi(id, request).getProject(), updated)
    }, 'PATCH'),
    projectActions: {
      createProject: (markdown) => write(projectsPath, { markdown }, projectResponse()),
      saveRequirement: (id, body) => write(`${projectPath(id)}/requirements`, body, projectResponse(id)),
      startRace: (id, requirement_version) => write(`${projectPath(id)}/race`, { requirement_version }, projectResponse(id)),
      saveSpecification: (id, body) => write(`${projectPath(id)}/specifications`, body, async (response) => {
        mapV2Specification(await response.json())
        return getSnapshot(id)
      }),
      confirmSpecification: (id, ref) => write(
        `${projectPath(id)}/specifications/${encodeURIComponent(ref.id)}/versions/${ref.version}/confirm`,
        {}, async (response) => {
          const result = mapV2Specification(await response.json())
          if (result.status !== 'confirmed' || !sameVersion(result, ref)) {
            throw new WorkbenchApiError('规格确认响应与请求版本不一致。', 'INVALID_RESPONSE', 0)
          }
          return getSnapshot(id)
        },
      ),
    },
  }
}

export function createV2Writer<T>(request: typeof fetch) {
  const writeKeys = new Map<string, string>()
  const pendingWrites = new Map<string, Promise<T>>()

  return function write(path: string, body: object, apply: (response: Response) => Promise<T>, method = 'POST') {
    const payload = JSON.stringify(body)
    const operation = `${method} ${path}\n${payload}`
    const pending = pendingWrites.get(operation)
    if (pending) return pending
    const key = writeKeys.get(operation) ?? crypto.randomUUID()
    writeKeys.set(operation, key)
    const submission = withApiDeadline(async (signal) => {
      const response = await request(path, {
        method, signal,
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: payload,
      })
      if (!response.ok) {
        if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          writeKeys.delete(operation)
        }
        throw await responseError(response, method, path)
      }
      return apply(response)
    }).then((snapshot) => {
      writeKeys.delete(operation)
      return snapshot
    }).finally(() => pendingWrites.delete(operation))
    pendingWrites.set(operation, submission)
    return submission
  }

}
