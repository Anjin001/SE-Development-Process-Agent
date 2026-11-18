import { expect, it, vi } from 'vitest'
import { fixture } from '../mocks/scenario'
import { readRunChanges } from './runMonitoring'

const event = fixture.approval_event
const next = { ...event, seq: 11, type: 'task.dispatched', task_id: 'T4' }

it('从快照游标逐页读取，去重后只刷新一次运行', async () => {
  const getEvents = vi.fn()
    .mockResolvedValueOnce({ items: [event, event], next_after: 10, has_more: true })
    .mockResolvedValueOnce({ items: [event, next, next], next_after: 11, has_more: false })
  const getRun = vi.fn().mockResolvedValue({ ...fixture.review_response, latest_event_seq: 11 })
  const response = await readRunChanges({ getEvents, getRun }, 'project', event.run_id, 9)
  expect(getEvents.mock.calls.map(([, , cursor]) => cursor)).toEqual([9, 10])
  expect(getRun).toHaveBeenCalledOnce()
  expect(response?.cursor).toBe(11)
  expect(response?.events).toEqual([event, next])
})

it('缺失事件或快照落后时不提交游标，重读后可恢复', async () => {
  const getEvents = vi.fn()
    .mockResolvedValueOnce({ items: [next], next_after: 11, has_more: false })
    .mockResolvedValue({ items: [event, next], next_after: 11, has_more: false })
  const getRun = vi.fn()
    .mockResolvedValueOnce(fixture.review_snapshot)
    .mockResolvedValueOnce({ ...fixture.review_response, latest_event_seq: 11 })
  const reads = { getEvents, getRun }
  await expect(readRunChanges(reads, 'project', event.run_id, 9)).rejects.toThrow('不连续')
  await expect(readRunChanges(reads, 'project', event.run_id, 9)).rejects.toThrow('尚未追上')
  expect((await readRunChanges(reads, 'project', event.run_id, 9))?.events).toEqual([event, next])
  expect(getEvents.mock.calls.map(([, , cursor]) => cursor)).toEqual([9, 9, 9])
})
