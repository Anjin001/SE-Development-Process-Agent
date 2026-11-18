export interface VersionRef {
  id: string
  version: number
}

export interface RequirementItem {
  id: string
  title: string
  text: string
  source: { section: string; start_line: number; end_line: number }
}

export interface Requirement extends VersionRef {
  markdown: string
  items: readonly RequirementItem[]
}

export type GroupId = 'A' | 'B'

export interface Draft {
  id: GroupId
  requirement_ref: VersionRef
  title: string
  summary: string
  claims: readonly { id: string; text: string; requirement_ids: readonly string[] }[]
}

export interface Evaluation {
  draft_id: GroupId
  criteria: readonly {
    criterion: 'value' | 'feasibility' | 'cost'
    score: 1 | 2 | 3 | 4 | 5
    reason: string
    claim_ids: readonly string[]
  }[]
}

export interface Race {
  requirement_ref: VersionRef
  groups: readonly {
    group_id: GroupId
    state: 'pending' | 'running' | 'completed' | 'failed'
    error: { code: string; message: string } | null
  }[]
}

export interface Specification extends VersionRef {
  status: 'draft' | 'confirmed'
  requirement_ref: VersionRef
  selection: GroupId | 'merge'
  markdown: string
  decisions: readonly {
    id: string
    scope: string
    choice: string
    reason: string
    requirement_ids: readonly string[]
    draft_ids: readonly GroupId[]
  }[]
  open_items: readonly {
    id: string
    question: string
    blocking: boolean
    affected_requirement_ids: readonly string[]
    resolution: string | null
  }[]
  confirmed_by: string | null
  confirmed_at: string | null
}

export type ArtifactKind = 'git_commit' | 'text'

export interface Task {
  id: string
  title: string
  phase: string
  requirement_ids: readonly string[]
  decision_ids: readonly string[]
  open_item_ids: readonly string[]
  depends_on: readonly string[]
  role: string
  write_paths: readonly string[]
  outputs: readonly { id: string; kind: ArtifactKind; path: string | null }[]
  estimate_seconds: number | null
  review_required: boolean
  completion_condition: string
}

export interface Plan extends VersionRef {
  spec_ref: VersionRef
  base_commit: string | null
  max_parallel: number
  tasks: readonly Task[]
}

export interface ScheduleEntry {
  task_id: string
  early_start_seconds: number
  early_finish_seconds: number
  critical: boolean
}

export interface PlanResponse {
  plan: Plan
  schedule: readonly ScheduleEntry[] | null
}

export interface UpdatePlanRequest {
  expected_version: number
  max_parallel?: number
  tasks?: readonly Task[]
}

export type TaskState = 'waiting' | 'ready' | 'running' | 'review' | 'failed' | 'input_required' | 'blocked' | 'done'

export interface Blocker {
  code: string
  ref_id: string
  message: string
}

export interface Attempt {
  id: string
  number: number
  state: 'running' | 'succeeded' | 'failed' | 'input_required'
  created_at: string
  started_at: string | null
  ended_at: string | null
  error: { code: string; message: string; retryable: boolean; details: Record<string, unknown> } | null
  input_refs: readonly (VersionRef & { commit_sha: string | null })[]
}

export interface Artifact extends VersionRef {
  task_id: string
  attempt_id: string
  output_id: string
  kind: ArtifactKind
  origin: 'agent' | 'human'
  status: 'draft' | 'approved' | 'rejected'
  based_on_version: number | null
  commit_sha: string | null
  changed_paths: readonly string[]
  content: string | null
}

export interface ReviewRecord {
  task_id: string
  decision: 'approve' | 'revise' | 'reject'
  expected_versions: Readonly<Record<string, number>>
  approved_versions: Readonly<Record<string, number>>
  note: string
  actor_id: string
  created_at: string
}

export interface RunTask {
  task_id: string
  state: TaskState
  attempts: readonly Attempt[]
  artifacts: readonly Artifact[]
  blockers: readonly Blocker[]
  missing_input_ids: readonly string[]
  review_records: readonly ReviewRecord[]
}

export interface Finding {
  stage: string
  code: string
  severity: 'info' | 'warning' | 'error'
  summary: string
  evidence_refs: readonly unknown[]
  affected_task_ids: readonly string[]
  suggested_action: string
}

export interface RunSummary {
  id: string
  plan_ref: VersionRef
  state: 'active' | 'completed'
  started_at: string | null
  ended_at: string | null
}

export interface Run extends RunSummary {
  mode: 'auto' | 'manual'
  dispatch: 'active' | 'paused'
  revision: number
  latest_event_seq: number
  tasks: readonly RunTask[]
  findings: readonly Finding[]
}

export interface StartRunRequest {
  plan_ref: VersionRef
  mode: Run['mode']
}

export type RunCommand = { expected_revision: number } & (
  | { action: 'pause' | 'resume' | 'step' }
  | { action: 'retry'; task_id: string }
  | { action: 'supply-input'; task_id: string; values: Readonly<Record<string, string>> }
  | ReviewCommand
)

export interface ReviewCommand {
  action: 'review'
  task_id: string
  decision: ReviewRecord['decision']
  expected_versions: Readonly<Record<string, number>>
  revisions: Readonly<Record<string, { content: string } | { commit_sha: string }>>
  note: string
}

export type ProjectStage = 'intake' | 'race' | 'specification' | 'planning' | 'execution' | 'completed'

export interface ProjectSnapshot {
  id: string
  stage: ProjectStage
  requirement: Requirement
  race: Race | null
  drafts: readonly Draft[]
  evaluations: readonly Evaluation[]
  specification: Specification | null
  latest_plan_ref: VersionRef | null
  runs: readonly RunSummary[]
  findings: readonly Finding[]
}

export interface RunEvent {
  seq: number
  type: string
  run_id: string
  task_id: string | null
  attempt_id: string | null
  created_at: string
  payload: Readonly<Record<string, unknown>>
}

export interface RunEventsPage {
  items: readonly RunEvent[]
  next_after: number
  has_more: boolean
}

export type SpecificationSelection = Specification['selection']

export type SaveSpecificationRequest = {
  requirement_version: number
  expected_version: number
  selection: SpecificationSelection
} & (
  | { markdown?: never; decisions?: never; open_items?: never }
  | Pick<Specification, 'markdown' | 'decisions' | 'open_items'>
)

export interface SaveRequirementRequest {
  expected_version: number
  markdown: string
}
