import type { RunEvent } from '../types/v2'
import type { RunReads } from './workbenchApi'

export async function readRunChanges(
  reads: RunReads, projectId: string, runId: string, after: number, isCurrent = () => true,
) {
  let cursor = after
  const events = new Map<number, RunEvent>()
  // Bound a single request cycle; the next cycle continues from the returned cursor.
  for (let pageNumber = 0; pageNumber < 4; pageNumber++) {
    const page = await reads.getEvents(projectId, runId, cursor)
    if (!isCurrent()) return null
    if (!Number.isSafeInteger(page.next_after) || page.next_after < cursor
        || (page.has_more && page.next_after === cursor)) {
      throw new Error('增量事件游标无效或未推进。')
    }

    const pending = new Map<number, RunEvent>()
    for (const event of page.items) {
      if (event.run_id !== runId || !Number.isSafeInteger(event.seq) || event.seq < 1 || event.seq > page.next_after) {
        throw new Error('运行事件归属或序号无效。')
      }
      if (event.seq <= cursor) continue
      const confirmed = pending.get(event.seq)
      if (confirmed && JSON.stringify(confirmed) !== JSON.stringify(event)) {
        throw new Error('同一序号包含冲突的运行事件。')
      }
      pending.set(event.seq, event)
    }
    if (pending.size !== page.next_after - cursor) throw new Error('返回的运行事件不连续。')
    for (const [seq, event] of pending) events.set(seq, event)
    cursor = page.next_after
    if (!page.has_more) break
  }
  if (cursor === after) return null
  const run = await reads.getRun(projectId, runId)
  if (!isCurrent()) return null
  if (run.id !== runId || run.latest_event_seq < cursor) throw new Error('运行快照尚未追上事件游标。')
  return { cursor, run, events: [...events.values()].sort((a, b) => a.seq - b.seq) }
}
