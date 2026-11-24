import type { PlanResponse, ProjectSnapshot, Run, RunEvent } from '../types/v2'
import type { StageId, StageSummary, TaskPreview, WorkbenchPlan, WorkbenchSnapshot } from '../types/workbench'
import { currentSpecification, sameVersion } from './projectVersions'

const stages: readonly Omit<StageSummary, 'status'>[] = [
  { id: 'intake', label: '需求录入', description: '提交 Markdown 需求，核对版本和原文来源。' },
  { id: 'race', label: '赛马出案', description: '查看两组进度，对比已完成的方案与评分。' },
  { id: 'review', label: '评审与整合', description: '选择方案，编辑并确认开发规格。' },
  { id: 'planning', label: '任务分解', description: '预留任务草案和规格来源的展示位置。' },
  { id: 'workflow', label: 'Workflow 编排', description: '查看任务依赖、计划概要和当前交接。' },
  { id: 'execution', label: '开发执行', description: '查看任务状态、阻塞原因和交付版本。' },
]

function currentStage(project: ProjectSnapshot): StageId {
  switch (project.stage) {
    case 'intake': return 'intake'
    case 'race': return 'race'
    case 'specification': return 'review'
    case 'planning': return project.latest_plan_ref ? 'workflow' : 'planning'
    case 'execution':
    case 'completed': return 'execution'
  }
}

function mapPlan(response: PlanResponse, run: Run | null): WorkbenchPlan {
  const { plan, schedule } = response
  const runTasks = new Map(run?.tasks.map((task) => [task.task_id, task]))
  const scheduledTasks = new Map(schedule?.map((entry) => [entry.task_id, entry]))
  const tasks = plan.tasks.map((task): TaskPreview => {
    const runtime = runTasks.get(task.id)
    return {
      id: task.id,
      title: task.title,
      role: task.role,
      status: runtime?.state ?? 'unstarted',
      estimateSeconds: task.estimate_seconds,
      requirementIds: task.requirement_ids,
      decisionIds: task.decision_ids,
      outputs: task.outputs,
      dependencies: task.depends_on,
      writePaths: task.write_paths,
      completionCondition: task.completion_condition,
      reviewRequired: task.review_required,
      onCriticalPath: scheduledTasks.get(task.id)?.critical ?? false,
      blockers: runtime?.blockers ?? [],
      missingInputIds: runtime?.missing_input_ids ?? [],
      attempts: runtime?.attempts ?? [],
      artifacts: runtime?.artifacts ?? [],
      reviewRecords: runtime?.review_records ?? [],
    }
  })
  return {
    ref: { id: plan.id, version: plan.version },
    specRef: plan.spec_ref,
    concurrency: plan.max_parallel,
    plannedSeconds: schedule?.reduce((end, entry) => Math.max(end, entry.early_finish_seconds), 0) ?? null,
    tasks,
    source: response,
  }
}

const eventTitles: Readonly<Record<string, string>> = {
  'run.started': '运行已启动',
  'run.paused': '派发已暂停',
  'run.resumed': '派发已恢复',
  'run.completed': '运行已完成',
  'task.dispatched': '任务已派发',
  'task.ready': '任务已就绪',
  'task.completed': '任务已完成',
  'task.failed': '任务失败',
  'task.input_required': '任务待补充输入',
  'task.input_supplied': '已补充输入',
  'task.retry_requested': '已请求重试',
  'artifact.submitted': '产物已提交',
  'artifact.approved': '产物已批准',
  'artifact.rejected': '产物已退回',
}

function eventsForRun(run: Run | null, events: readonly RunEvent[]) {
  if (!run) return []
  const matching = events.filter((event) => event.run_id === run.id && event.seq <= run.latest_event_seq)
  const unique = new Map(matching.map((event) => [event.seq, event]))
  return [...unique.values()].sort((left, right) => left.seq - right.seq)
}

function mapActivities(events: readonly RunEvent[]): WorkbenchSnapshot['activities'] {
  return events.map((event) => ({
    id: `${event.run_id}:${event.seq}`,
    time: event.created_at,
    title: Object.hasOwn(eventTitles, event.type) ? eventTitles[event.type] : event.type,
    description: [event.run_id, event.task_id, event.attempt_id].filter(Boolean).join(' · '),
  }))
}

export function mapWorkbenchSnapshot(
  project: ProjectSnapshot,
  planResponse: PlanResponse | null,
  run: Run | null,
  events: readonly RunEvent[],
  latestPlanResponse = planResponse,
): WorkbenchSnapshot {
  const activeStage = currentStage(project)
  const activeIndex = stages.findIndex(({ id }) => id === activeStage)
  const planRef = run?.plan_ref ?? project.latest_plan_ref
  const plan = planResponse && sameVersion(planResponse.plan, planRef) ? mapPlan(planResponse, run) : null
  const latestPlan = plan && sameVersion(plan.ref, project.latest_plan_ref) ? plan
    : latestPlanResponse && sameVersion(latestPlanResponse.plan, project.latest_plan_ref)
      ? mapPlan(latestPlanResponse, null) : null
  const specification = currentSpecification(project)
  const title = project.requirement.markdown.match(/^#[ \t]+(.+)$/m)?.[1].trim()
  const runEvents = eventsForRun(run, events)

  return {
    projectData: project,
    project: { name: title || project.id, reference: project.id },
    activeStage,
    stages: stages.map((stage, index) => ({
      ...stage,
      status: project.stage === 'completed' || index < activeIndex ? 'complete' : index === activeIndex ? 'active' : 'waiting',
    })),
    plan,
    latestPlan,
    specificationRef: specification && { id: specification.id, version: specification.version },
    run,
    runEvents,
    findings: [...project.findings, ...(run?.findings ?? [])],
    activities: mapActivities(runEvents),
  }
}

export function isRunCurrent(current: Run, incoming: Run) {
  return current.id === incoming.id && sameVersion(current.plan_ref, incoming.plan_ref)
    && incoming.revision >= current.revision && incoming.latest_event_seq >= current.latest_event_seq
    && (incoming.revision !== current.revision || incoming.latest_event_seq === current.latest_event_seq)
    && (current.state === 'active' || incoming.state === current.state)
}

export function applyRunUpdate(snapshot: WorkbenchSnapshot, incoming: Run, events: readonly RunEvent[]) {
  if (snapshot.run?.id !== incoming.id || !sameVersion(snapshot.run.plan_ref, incoming.plan_ref)) return snapshot
  const run = incoming.revision > snapshot.run.revision && isRunCurrent(snapshot.run, incoming) ? incoming : snapshot.run
  const { id, plan_ref, state, started_at, ended_at } = run
  const project = {
    ...snapshot.projectData,
    stage: snapshot.projectData.stage === 'execution' && state === 'completed' ? 'completed' as const : snapshot.projectData.stage,
    runs: snapshot.projectData.runs.map((item) => item.id === id ? { id, plan_ref, state, started_at, ended_at } : item),
  }
  return mapWorkbenchSnapshot(project, snapshot.plan?.source ?? null, run, [...events, ...snapshot.runEvents], snapshot.latestPlan?.source ?? null)
}
