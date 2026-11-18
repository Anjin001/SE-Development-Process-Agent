import type { Artifact, Attempt, Blocker, Finding, PlanResponse, ProjectSnapshot, ReviewRecord, Run, RunEvent, Task, TaskState, VersionRef } from './v2'

export type StageId = 'intake' | 'race' | 'review' | 'planning' | 'workflow' | 'execution'
export type StageStatus = 'complete' | 'active' | 'waiting'
export type WorkbenchView = 'dependencies' | 'timeline' | 'configuration' | 'delivery'

export interface StageSummary {
  id: StageId
  label: string
  status: StageStatus
  description: string
}

export interface TaskPreview {
  id: string
  title: string
  role: string
  status: TaskState | 'unstarted'
  estimateSeconds: number | null
  requirementIds: readonly string[]
  decisionIds: readonly string[]
  outputs: Task['outputs']
  dependencies: readonly string[]
  writePaths: readonly string[]
  completionCondition: string
  reviewRequired: boolean
  onCriticalPath: boolean
  blockers: readonly Blocker[]
  missingInputIds: readonly string[]
  attempts: readonly Attempt[]
  artifacts: readonly Artifact[]
  reviewRecords: readonly ReviewRecord[]
}

export interface ActivityEntry {
  id: string
  time: string
  title: string
  description: string
}

export interface WorkbenchPlan {
  ref: VersionRef
  specRef: VersionRef
  tasks: readonly TaskPreview[]
  plannedSeconds: number | null
  concurrency: number
  source: PlanResponse
}

export interface WorkbenchSnapshot {
  projectData: ProjectSnapshot
  project: { name: string; reference: string }
  activeStage: StageId
  stages: readonly StageSummary[]
  plan: WorkbenchPlan | null
  latestPlan: WorkbenchPlan | null
  specificationRef: VersionRef | null
  run: Run | null
  runEvents: readonly RunEvent[]
  findings: readonly Finding[]
  activities: readonly ActivityEntry[]
}
