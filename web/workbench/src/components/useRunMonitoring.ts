import { useEffect, useEffectEvent, useState } from 'react'
import type { RefObject } from 'react'
import { isRunCurrent } from '../api/mapWorkbenchSnapshot'
import { startPolling } from '../api/polling'
import { readRunChanges } from '../api/runMonitoring'
import type { WorkbenchApi } from '../api/workbenchApi'
import type { Run, RunEvent } from '../types/v2'
import type { WorkbenchSnapshot } from '../types/workbench'

export function useRunMonitoring(
  snapshot: WorkbenchSnapshot | null,
  api: WorkbenchApi,
  writing: RefObject<boolean>,
  generation: RefObject<number>,
  onUpdate: (run: Run, events: readonly RunEvent[], projectId: string) => void,
  onRecovery: (snapshot: WorkbenchSnapshot, projectId: string) => void,
  interval: number,
) {
  const [status, setStatus] = useState<{ key: string; message: string; ok: boolean }>()
  const active = snapshot?.run?.state === 'active'
  const id = snapshot?.run?.id
  const projectId = snapshot?.projectData.id
  const key = id && projectId ? JSON.stringify([projectId, id]) : undefined
  const currentRun = useEffectEvent(() => snapshot?.run)
  const update = useEffectEvent(onUpdate)
  const recoverProject = useEffectEvent(onRecovery)

  useEffect(() => {
    const initial = currentRun()
    const reads = api.runReads
    if (!reads || !id || !projectId || !key || initial?.state !== 'active') return
    let cursor = initial.latest_event_seq
    let recoveredSnapshot = false

    return startPolling({
      interval,
      async poll(recovering, isActive) {
        if (writing.current) return false
        const before = currentRun()
        if (before && before.state !== 'active' && cursor >= before.latest_event_seq) return 'done'
        const requestGeneration = generation.current
        const isCurrent = () => isActive() && requestGeneration === generation.current
        if (recovering && !recoveredSnapshot) {
          const refreshed = await api.getSnapshot(projectId)
          if (!isCurrent()) return false
          if (refreshed.projectData.id !== projectId) throw new Error('恢复快照不属于当前项目。')
          recoverProject(refreshed, projectId)
          recoveredSnapshot = true
        }

        const change = await readRunChanges(reads, projectId, id, cursor, isCurrent)
        if (!isCurrent()) return false
        if (change) {
          const present = currentRun() ?? initial
          const incoming = change.run
          // A full refresh may already show a newer snapshot. Older, verified event
          // pages are still useful, but they must never replace that snapshot.
          if (incoming.plan_ref.id !== initial.plan_ref.id || incoming.plan_ref.version !== initial.plan_ref.version
              || incoming.id !== present.id || (incoming.latest_event_seq > present.latest_event_seq
                && !isRunCurrent(present, incoming))) {
            throw new Error('运行快照与当前监控的版本不一致。')
          }
          update(incoming, change.events, projectId)
          cursor = change.cursor
        }
        recoveredSnapshot = false
        setStatus((previous) => previous?.ok && previous.key === key ? previous
          : { key, ok: true, message: '运行监控已连接' })
      },
      onError(error) {
        setStatus({ key, ok: false, message: error instanceof Error ? error.message : '读取运行事件失败。' })
      },
    })
  }, [api, id, projectId, key, writing, generation, interval])

  return api.runReads && key && active ? (status?.key === key ? status : { key, ok: true, message: '正在连接运行监控…' }) : null
}
