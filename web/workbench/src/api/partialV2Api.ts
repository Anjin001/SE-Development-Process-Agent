import type { PlanResponse, UpdatePlanRequest, VersionRef } from '../types/v2'
import { mapWorkbenchSnapshot } from './mapWorkbenchSnapshot'
import { mapV2Events, mapV2Plan, mapV2Project } from './v2Mapping'
import { createV2ReadApi, createV2Writer, WorkbenchApiError } from './workbenchApi'
import type { WorkbenchApi } from './workbenchApi'

export function createPartialV2Api(projectId: string, request: typeof fetch = fetch) {
  const reads = createV2ReadApi(projectId, request)
  const path = (id: string) => `/api/v2/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(id)}`
  const write = createV2Writer<PlanResponse>(request)
  return {
    getProject: async () => mapV2Project(await reads.getProject(), projectId),
    getPlan: async (ref: VersionRef) => mapV2Plan(await reads.getPlan(ref), ref.id, ref.version),
    getEvents: async (runId: string, after = 0, limit = 100) => mapV2Events(await reads.getEvents(runId, after, limit), runId, after),
    patchPlan: (planId: string, body: UpdatePlanRequest) => write(path(planId), body, async (response) => {
      const result = mapV2Plan(await response.json() as PlanResponse, planId)
      if (result.plan.version <= body.expected_version) {
        throw new WorkbenchApiError('服务未返回更新后的计划版本。', 'INVALID_RESPONSE', 0)
      }
      return result
    }, 'PATCH'),
  }
}

// Keep the raw run, artifact and review APIs outside this adapter until the
// shared HTTP contract matches the workbench's snapshot and review payloads.
export function createPartialWorkbench(projectId: string, api: ReturnType<typeof createPartialV2Api>, writablePlanId?: string): WorkbenchApi {
  return {
    async getSnapshot() {
      const project = await api.getProject()
      const plan = project.latest_plan_ref ? await api.getPlan(project.latest_plan_ref) : null
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
      return mapWorkbenchSnapshot({ ...project, latest_plan_ref: { id: response.plan.id, version: response.plan.version } }, response, null, [])
    } : undefined,
  }
}
