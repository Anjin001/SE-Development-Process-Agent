import type { PlanResponse, ProjectSnapshot, Run, RunCommand, RunEventsPage, SaveRequirementRequest, SaveSpecificationRequest, StartRunRequest, UpdatePlanRequest, VersionRef } from '../types/v2'
import type { WorkbenchSnapshot } from '../types/workbench'
import { mapWorkbenchSnapshot } from './mapWorkbenchSnapshot'
import { sameVersion } from './projectVersions'

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

export class WorkbenchApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Readonly<Record<string, unknown>>

  constructor(message: string, code: string, status: number, details: Readonly<Record<string, unknown>> = {}) {
    super(message)
    this.name = 'WorkbenchApiError'
    this.code = code
    this.status = status
    this.details = details
  }
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

  function read<T>(path: string): Promise<T> {
    return withApiDeadline(async (signal) => {
      const response = await request(path, { signal, headers: { Accept: 'application/json' } })
      if (!response.ok) throw await responseError(response, 'GET', path)
      return response.json() as Promise<T>
    })
  }

  return {
    getProject: () => read<ProjectSnapshot>(path),
    getPlan: (ref: VersionRef) => read<PlanResponse>(`${path}/plans/${encodeURIComponent(ref.id)}/versions/${ref.version}`),
    getRun: (id: string) => read<Run>(runPath(id)),
    getEvents: (id: string, after = 0, limit = 100) => read<RunEventsPage>(`${runPath(id)}/events?after=${after}&limit=${limit}`),
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
    return mapWorkbenchSnapshot(project, plan, run, events?.items ?? [], sameVersion(planRef, latestRef) ? plan : latestPlan)
  }

  async function getSnapshot(id = projectId) {
    return loadProject(await createV2ReadApi(id, request).getProject())
  }

  const projectResponse = async (response: Response) => loadProject(await response.json() as ProjectSnapshot)
  const runResponse = (id: string) => async (response: Response) => {
    const run = await response.json() as Run
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
        `${projectPath(id)}/runs/${encodeURIComponent(runId)}/actions/${action}`, body, runResponse(id),
      ),
    },
    savePlan: (id, planId, body) => write(`${projectPath(id)}/plans/${encodeURIComponent(planId)}`, body, async (response) => {
      const updated = await response.json() as PlanResponse
      return loadProject(await createV2ReadApi(id, request).getProject(), updated)
    }, 'PATCH'),
    projectActions: {
      createProject: (markdown) => write(projectsPath, { markdown }, projectResponse),
      saveRequirement: (id, body) => write(`${projectPath(id)}/requirements`, body, projectResponse),
      startRace: (id, requirement_version) => write(`${projectPath(id)}/race`, { requirement_version }, projectResponse),
      saveSpecification: (id, body) => write(`${projectPath(id)}/specifications`, body, () => getSnapshot(id)),
      confirmSpecification: (id, ref) => write(
        `${projectPath(id)}/specifications/${encodeURIComponent(ref.id)}/versions/${ref.version}/confirm`,
        {}, () => getSnapshot(id),
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
