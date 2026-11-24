import type { PlanResponse, ProjectSnapshot, Run, RunEventsPage, Specification } from '../types/v2'
import { currentSpecification, sameVersion } from './projectVersions'
import { WorkbenchApiError } from './workbenchApiError'

type Check = (value: unknown) => boolean

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const fields = (shape: Record<string, Check>): Check => (value) =>
  record(value) && Object.entries(shape).every(([key, check]) => check(value[key]))
const list = (check: Check): Check => (value) => Array.isArray(value) && value.every(check)
const nullable = (check: Check): Check => (value) => value === null || check(value)
const oneOf = (...options: string[]): Check => (value) => typeof value === 'string' && options.includes(value)
const text: Check = (value) => typeof value === 'string'
const id: Check = (value) => typeof value === 'string' && value.trim().length > 0
const count: Check = (value) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const positive: Check = (value) => count(value) && value !== 0
const flag: Check = (value) => typeof value === 'boolean'
const ids = list(id)
const ref = { id, version: positive }
const versionRef = fields(ref)
const sha: Check = (value) => typeof value === 'string' && /^[0-9a-f]{40}$/i.test(value)
const error = { code: id, message: text }
const group = oneOf('A', 'B')
const artifactKind = oneOf('git_commit', 'text')
const reviewDecision = oneOf('approve', 'revise', 'reject')
const runState = oneOf('active', 'completed', 'stopped')
const runSummary = { id, plan_ref: versionRef, state: runState, started_at: nullable(text), ended_at: nullable(text) }
const findings = list(fields({
  stage: id, code: id, severity: oneOf('info', 'warning', 'error'), summary: text,
  evidence_refs: Array.isArray, affected_task_ids: ids, suggested_action: text,
}))
const specification = fields({
  ...ref, status: oneOf('draft', 'confirmed'), requirement_ref: versionRef, selection: oneOf('A', 'B', 'merge'),
  markdown: text, decisions: list(fields({ id, scope: text, choice: text, reason: text,
    requirement_ids: ids, draft_ids: list(group) })),
  open_items: list(fields({ id, question: text, blocking: flag, affected_requirement_ids: ids, resolution: nullable(text) })),
  confirmed_by: nullable(text), confirmed_at: nullable(text),
})
const project = fields({
  id, stage: oneOf('intake', 'race', 'specification', 'planning', 'execution', 'completed'),
  requirement: fields({ ...ref, markdown: text, items: list(fields({ id, title: text, text,
    source: fields({ section: text, start_line: positive, end_line: positive }) })) }),
  race: nullable(fields({ requirement_ref: versionRef, groups: list(fields({
    group_id: group, state: oneOf('pending', 'running', 'completed', 'failed'), error: nullable(fields(error)),
  })) })),
  drafts: list(fields({ id: group, requirement_ref: versionRef, title: text, summary: text,
    claims: list(fields({ id, text, requirement_ids: ids })) })),
  evaluations: list(fields({ draft_id: group, criteria: list(fields({
    criterion: oneOf('value', 'feasibility', 'cost'), score: (value) => positive(value) && Number(value) <= 5,
    reason: text, claim_ids: ids,
  })) })),
  specification: nullable(specification), latest_plan_ref: nullable(versionRef), runs: list(fields(runSummary)), findings,
})
const task = fields({
  id, title: text, phase: id, requirement_ids: ids, decision_ids: ids, open_item_ids: ids, depends_on: ids,
  role: id, write_paths: ids, outputs: list(fields({ id, kind: artifactKind, path: nullable(text) })),
  estimate_seconds: nullable(count), review_required: flag, completion_condition: text,
})
const plan = fields({
  plan: fields({ ...ref, spec_ref: versionRef, base_commit: nullable(sha), max_parallel: positive, tasks: list(task) }),
  schedule: nullable(list(fields({ task_id: id, early_start_seconds: count, early_finish_seconds: count, critical: flag }))),
})
const versions: Check = (value) => record(value) && Object.values(value).every(positive)
const artifact = fields({
  ...ref, task_id: id, attempt_id: id, output_id: id, kind: artifactKind, origin: oneOf('agent', 'human'),
  status: oneOf('draft', 'approved', 'rejected'), based_on_version: nullable(positive),
  commit_sha: nullable(sha), changed_paths: ids, content: nullable(text),
})
const runTask = fields({
  task_id: id, state: oneOf('waiting', 'ready', 'running', 'review', 'failed', 'input_required', 'blocked', 'done'),
  attempts: list(fields({ id, number: positive, state: oneOf('running', 'succeeded', 'failed', 'input_required'),
    created_at: id, started_at: nullable(text), ended_at: nullable(text),
    error: nullable(fields({ ...error, retryable: flag, details: record })),
    input_refs: list(fields({ ...ref, commit_sha: nullable(sha) })) })),
  artifacts: list(artifact),
  blockers: list(fields({ ...error, ref_id: id })), missing_input_ids: ids,
  review_records: list(fields({ task_id: id, decision: reviewDecision, expected_versions: versions,
    approved_versions: versions, note: text, actor_id: id, created_at: id })),
})
const run = fields({
  ...runSummary, mode: oneOf('auto', 'manual'), dispatch: oneOf('active', 'paused'),
  revision: positive, latest_event_seq: count, tasks: list(runTask), findings,
})
const event = fields({ seq: positive, type: id, run_id: id, task_id: nullable(id), attempt_id: nullable(id),
  created_at: id, payload: record })
const events = fields({ items: list(event), next_after: count, has_more: flag })

function invalid(message: string): never {
  throw new WorkbenchApiError(message, 'INVALID_RESPONSE', 0)
}

export function mapV2Project(value: unknown, projectId?: string): ProjectSnapshot {
  if (!project(value)) invalid('项目快照缺少 v2 字段、字段类型无效或包含当前工作台不支持的小组。')
  const snapshot = value as ProjectSnapshot
  if (projectId !== undefined && snapshot.id !== projectId) invalid('项目快照与请求的项目不一致。')
  if (snapshot.race && !sameVersion(snapshot.race.requirement_ref, snapshot.requirement)) {
    invalid('赛马引用的需求版本与当前需求不一致。')
  }
  if (snapshot.specification && (snapshot.specification.requirement_ref.id !== snapshot.requirement.id
      || snapshot.specification.requirement_ref.version > snapshot.requirement.version)) {
    invalid('规格引用了不属于当前项目的需求或不存在的需求版本。')
  }
  return snapshot
}

export function mapV2Specification(value: unknown): Specification {
  if (!specification(value)) invalid('规格响应不符合 v2 契约。')
  return value as Specification
}

export function mapV2Plan(value: unknown, planId: string, version?: number): PlanResponse {
  if (!plan(value)) invalid('计划响应缺少 v2 字段或字段类型无效。')
  const result = value as PlanResponse
  if (result.plan.id !== planId || (version !== undefined && result.plan.version !== version)
      || result.schedule?.some((entry) => entry.early_finish_seconds < entry.early_start_seconds
        || !result.plan.tasks.some((task) => task.id === entry.task_id))) {
    invalid('计划响应与请求的版本或排期引用不一致。')
  }
  return result
}

export function mapV2UpdatedPlan(value: unknown, planId: string, previousVersion: number) {
  const result = mapV2Plan(value, planId)
  if (result.plan.version <= previousVersion) invalid('服务未返回更新后的计划版本。')
  return result
}

export function mapV2Run(value: unknown, runId?: string, projectId?: string): Run {
  if (!run(value)) invalid('运行快照不符合 v2 契约，请检查任务列表、尝试和产物版本。')
  const result = value as Run
  if (projectId !== undefined && record(value) && 'project_id' in value && value.project_id !== projectId) {
    invalid('运行快照不属于请求的项目。')
  }
  if (runId !== undefined && result.id !== runId) invalid('运行快照与请求的运行不一致。')
  if (result.tasks.some((task) => task.artifacts.some((item) =>
    item.task_id !== task.task_id || !task.attempts.some((attempt) => attempt.id === item.attempt_id)
    || (item.based_on_version !== null && item.based_on_version >= item.version)
    || (item.kind === 'text' ? item.commit_sha !== null || item.content === null || item.changed_paths.length > 0
      : item.commit_sha === null || item.content !== null)))) {
    invalid('运行快照的产物类型、版本或任务引用无效。')
  }
  const artifactKey = (id: string, version: number) => JSON.stringify([id, version])
  const artifacts = result.tasks.flatMap((task) => task.artifacts)
  const byVersion = new Map(artifacts.map((item) => [artifactKey(item.id, item.version), item]))
  if (byVersion.size !== artifacts.length || artifacts.some((item) => item.based_on_version !== null
      && byVersion.get(artifactKey(item.id, item.based_on_version))?.task_id !== item.task_id)
    || result.tasks.some((task) =>
    task.attempts.some((attempt) => attempt.input_refs.some((ref) =>
      byVersion.get(artifactKey(ref.id, ref.version))?.commit_sha !== ref.commit_sha))
    || task.review_records.some((review) => review.task_id !== task.task_id
      || [review.expected_versions, review.approved_versions].some((versions) =>
        Object.entries(versions).some(([id, version]) => byVersion.get(artifactKey(id, version))?.task_id !== task.task_id))))) {
    invalid('尝试输入或审核记录引用了不存在或不匹配的产物版本。')
  }
  return result
}

export function mapV2Events(value: unknown, runId: string, after: number): RunEventsPage {
  if (!events(value)) invalid('事件分页响应缺少 v2 字段或字段类型无效。')
  const result = value as RunEventsPage
  if (result.items.some((item, index) => item.run_id !== runId || item.seq !== after + index + 1)
      || result.next_after !== after + result.items.length || (result.has_more && !result.items.length)) {
    invalid('运行事件的游标或序号不连续。')
  }
  return result
}

export function validateV2Bindings(
  project: ProjectSnapshot, boundPlan: PlanResponse | null, latestPlan: PlanResponse | null, run: Run | null,
) {
  const spec = currentSpecification(project)
  if (latestPlan && (!sameVersion(latestPlan.plan, project.latest_plan_ref)
      || spec?.status !== 'confirmed' || !sameVersion(latestPlan.plan.spec_ref, spec))) {
    invalid('最新计划未绑定当前项目已确认的规格版本。')
  }
  if (latestPlan && spec) {
    const requirements = new Set(project.requirement.items.map(({ id }) => id))
    const decisions = new Set(spec.decisions.map(({ id }) => id))
    const openItems = new Set(spec.open_items.map(({ id }) => id))
    if (latestPlan.plan.tasks.some((task) => task.requirement_ids.some((id) => !requirements.has(id))
        || task.decision_ids.some((id) => !decisions.has(id)) || task.open_item_ids.some((id) => !openItems.has(id)))) {
      invalid('最新计划中的任务引用了当前需求或规格中不存在的条目。')
    }
  }
  if (!run) return
  const summary = project.runs.find(({ id }) => id === run.id)
  if (!boundPlan || !summary || !sameVersion(summary.plan_ref, run.plan_ref) || !sameVersion(boundPlan.plan, run.plan_ref)) {
    invalid('运行、项目运行摘要及绑定计划版本不一致。')
  }
  const tasks = new Map(boundPlan.plan.tasks.map((task) => [task.id, task]))
  const runtimeIds = new Set(run.tasks.map(({ task_id }) => task_id))
  if (runtimeIds.size !== run.tasks.length || runtimeIds.size !== tasks.size
      || run.tasks.some(({ task_id, artifacts }) => {
        const task = tasks.get(task_id)
        return !task || artifacts.some((item) => !task.outputs.some((output) => output.id === item.output_id && output.kind === item.kind))
      })) {
    invalid('运行任务或产物与绑定计划的任务及输出不一致。')
  }
}
