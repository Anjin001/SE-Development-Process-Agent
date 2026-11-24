import type { PlanResponse, UpdatePlanRequest } from '../types/v2'
import { mapWorkbenchSnapshot } from './mapWorkbenchSnapshot'
import { mapV2UpdatedPlan, validateV2Bindings } from './v2Mapping'
import { createV2ReadApi, createV2Writer, WorkbenchApiError } from './workbenchApi'
import type { WorkbenchApi } from './workbenchApi'

export function createPartialV2Api(projectId: string, request: typeof fetch = fetch) {
  const reads = createV2ReadApi(projectId, request)
  const path = (id: string) => `/api/v2/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(id)}`
  const write = createV2Writer<PlanResponse>(request)
  return {
    getProject: reads.getProject,
    getPlan: reads.getPlan,
    getEvents: reads.getEvents,
    patchPlan: (planId: string, body: UpdatePlanRequest) => write(path(planId), body, async (response) =>
      mapV2UpdatedPlan(await response.json(), planId, body.expected_version), 'PATCH'),
  }
}

// Keep the raw run, artifact and review APIs outside this adapter until the
// shared HTTP contract matches the workbench's snapshot and review payloads.
export function createPartialWorkbench(projectId: string, api: ReturnType<typeof createPartialV2Api>, writablePlanId?: string): WorkbenchApi {
  return {
    async getSnapshot(id = projectId) {
      if (id !== projectId) {
        throw new WorkbenchApiError('此连接未绑定请求的项目。', 'FORBIDDEN', 403)
      }
      const project = await api.getProject()
      const plan = project.latest_plan_ref ? await api.getPlan(project.latest_plan_ref) : null
      validateV2Bindings(project, plan, plan, null)
      return mapWorkbenchSnapshot(project, plan, null, [])
    },
    savePlan: writablePlanId ? async (id, planId, body) => {
      if (id !== projectId || planId !== writablePlanId) {
        throw new WorkbenchApiError('此计划没有在联调配置中授权写入。', 'FORBIDDEN', 403)
      }
      const project = await api.getProject()
      if (project.latest_plan_ref?.id !== planId) {
        throw new WorkbenchApiError('计划版本已更新。', 'VERSION_CONFLICT', 409)
      }
      const response = await api.patchPlan(planId, body)
      const updated = { ...project, latest_plan_ref: { id: response.plan.id, version: response.plan.version } }
      validateV2Bindings(updated, response, response, null)
      return mapWorkbenchSnapshot(updated, response, null, [])
    } : undefined,
  }
}
