import { createContext, useContext, useState } from 'react'
import type { SetStateAction } from 'react'

export const ProjectDrafts = createContext<{ projectId: string; values: Map<string, unknown> } | null>(null)

export function useProjectDraft<T>(name: string, initial: T, explicitScope?: { projectId: string; values: Map<string, unknown> }) {
  const context = useContext(ProjectDrafts)
  const scope = explicitScope ?? context
  const [local, setLocal] = useState(initial)
  const [, refresh] = useState(0)
  const key = JSON.stringify([scope?.projectId, name])
  const value = scope ? (scope.values.has(key) ? scope.values.get(key) as T : initial) : local

  function setValue(action: SetStateAction<T>) {
    if (!scope) {
      setLocal(action)
      return
    }
    const current = scope.values.has(key) ? scope.values.get(key) as T : initial
    const next = typeof action === 'function' ? (action as (previous: T) => T)(current) : action
    scope.values.set(key, next)
    refresh((value) => value + 1)
  }

  return [value, setValue] as const
}
