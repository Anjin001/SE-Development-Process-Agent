import { useEffect, useEffectEvent, useState } from 'react'
import type { RefObject } from 'react'
import { readRunChanges } from '../api/runMonitoring'
import type { RunReads } from '../api/workbenchApi'
import type { Run, RunEvent } from '../types/v2'
import type { WorkbenchSnapshot } from '../types/workbench'

export function useRunMonitoring(
  snapshot: WorkbenchSnapshot | null,
  reads: RunReads | undefined,
  writing: RefObject<boolean>,
  generation: RefObject<number>,
  onUpdate: (run: Run, events: readonly RunEvent[]) => void,
  interval: number,
) {
  const [status, setStatus] = useState<{ key: string; message: string; ok: boolean }>()
  const active = snapshot?.run?.state === 'active'
  const id = snapshot?.run?.id
  const projectId = snapshot?.projectData.id
  const key = id && projectId ? JSON.stringify([projectId, id]) : undefined
  const currentRun = useEffectEvent(() => snapshot?.run)
  const update = useEffectEvent(onUpdate)

  useEffect(() => {
    const initial = currentRun()
    if (!reads || !id || !projectId || !key || initial?.state !== 'active') return
    const source = reads
    const runId = id
    const project = projectId
    const syncKey = key
    let cursor = initial.latest_event_seq
    let stopped = false
    let running = false
    let timer: ReturnType<typeof setTimeout>

    async function poll() {
      if (stopped || running) return
      const present = currentRun()
      if (present?.state === 'completed' && cursor >= present.latest_event_seq) return
      running = true
      if (!writing.current) {
        const requestGeneration = generation.current
        try {
          const change = await readRunChanges(source, project, runId, cursor)
          if (!stopped && requestGeneration === generation.current) {
            if (change) {
              cursor = change.cursor
              update(change.run, change.events)
            }
            setStatus((previous) => previous?.ok && previous.key === syncKey ? previous
              : { key: syncKey, ok: true, message: '运行监控已连接' })
          }
        } catch (error) {
          if (!stopped && requestGeneration === generation.current) {
            setStatus({ key: syncKey, ok: false, message: error instanceof Error ? error.message : '读取运行事件失败。' })
          }
        }
      }
      running = false
      if (!stopped) timer = setTimeout(poll, interval)
    }

    function reconnect() {
      if (running) return
      clearTimeout(timer)
      void poll()
    }
    timer = setTimeout(poll, interval)
    window.addEventListener('online', reconnect)
    return () => {
      stopped = true
      clearTimeout(timer)
      window.removeEventListener('online', reconnect)
    }
  }, [reads, id, projectId, key, writing, generation, interval])

  return reads && key && active ? (status?.key === key ? status : { key, ok: true, message: '正在连接运行监控…' }) : null
}
