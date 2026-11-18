import type { RunEvent } from '../types/v2'
import type { RunReads } from './workbenchApi'

export async function readRunChanges(reads: RunReads, projectId: string, runId: string, after: number) {
  let cursor = after
  const events = new Map<number, RunEvent>()
  // Bound a single request cycle; the next cycle continues from the returned cursor.
  for (let pageNumber = 0; pageNumber < 4; pageNumber++) {
    const page = await reads.getEvents(projectId, runId, cursor)
    const previous = cursor
    const previousCount = events.size
    if (page.next_after > cursor) cursor = page.next_after
    for (const event of page.items) {
      if (event.run_id === runId && event.seq > previous && event.seq <= cursor) events.set(event.seq, event)
    }
    if (events.size - previousCount !== cursor - previous) throw new Error('返回的运行事件不连续。')
    if (!page.has_more) break
    if (cursor === previous) throw new Error('增量事件游标未推进。')
  }
  if (cursor === after) return null
  const run = await reads.getRun(projectId, runId)
  if (run.id !== runId || run.latest_event_seq < cursor) throw new Error('运行快照尚未追上事件游标。')
  return { cursor, run, events: [...events.values()].sort((a, b) => a.seq - b.seq) }
}
