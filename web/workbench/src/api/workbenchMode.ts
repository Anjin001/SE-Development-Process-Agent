import { mockWorkbenchApi } from './mockWorkbenchApi'
import { createPartialV2Api, createPartialWorkbench } from './partialV2Api'
import { WorkbenchApiError } from './workbenchApi'

export interface WorkbenchEnvironment {
  VITE_WORKBENCH_MODE?: string
  VITE_WORKBENCH_API_BASE?: string
  VITE_WORKBENCH_PROJECT_ID?: string
  VITE_WORKBENCH_PLAN_ID?: string
}

export async function resolveWorkbench(env: WorkbenchEnvironment, fetcher: typeof fetch = fetch) {
  const mode = env.VITE_WORKBENCH_MODE?.trim() || 'mock'
  if (mode === 'mock') return { api: mockWorkbenchApi, message: '' }
  if (mode !== 'v2') throw new Error('VITE_WORKBENCH_MODE 须为 mock 或 v2。')
  const projectId = env.VITE_WORKBENCH_PROJECT_ID?.trim()
  if (!projectId) throw new Error('v2 模式需要配置已有的测试项目 ID。')
  const base = env.VITE_WORKBENCH_API_BASE?.trim().replace(/\/+$/, '') ?? ''
  if (base) {
    const url = new URL(base)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
      throw new Error('API 地址仅填写 HTTP(S) 服务源，不包含路径、凭据或查询参数。')
    }
  }
  const client = createPartialV2Api(projectId, (path, init) => fetcher(`${base}${path}`, init))
  try {
    await client.getProject()
  } catch (error) {
    if (error instanceof WorkbenchApiError && error.status === 404) {
      return { api: mockWorkbenchApi,
        message: `标准项目入口 /api/v2/projects/${encodeURIComponent(projectId)} 返回 404。当前使用独立 Mock 演示，未连接真实项目。` }
    }
    throw error
  }
  return { api: createPartialWorkbench(projectId, client, env.VITE_WORKBENCH_PLAN_ID?.trim()),
    message: '真实 v2 局部模式。当前页面仅接入计划，运行、产物和审核仍待契约统一。' }
}
