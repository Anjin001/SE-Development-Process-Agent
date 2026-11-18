import scenario from '../../../../examples/v2/scenario.json'
import type { PlanResponse, ProjectSnapshot, Run, RunEvent, Task } from '../types/v2'

// JSON imports widen string literals. Keep the wire-type assertion at this boundary.
export const fixture = scenario as {
  project_snapshot: ProjectSnapshot
  plan_response: PlanResponse
  review_snapshot: Run
  review_response: Run
  approval_event: RunEvent
  errors: {
    plan_cycle: {
      status: number
      body: { error: { code: string; message: string; details: { cycle: readonly string[] } } }
      request: { body: { tasks: readonly Pick<Task, 'id' | 'depends_on'>[] } }
    }
  }
}
