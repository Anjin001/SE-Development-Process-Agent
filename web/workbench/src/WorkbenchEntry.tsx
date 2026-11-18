import { useEffect, useState } from 'react'
import App from './App'
import { resolveWorkbench } from './api/workbenchMode'
import { describeError } from './api/workbenchFeedback'
import { WorkbenchApiError } from './api/workbenchApi'

export function WorkbenchEntry() {
  const [result, setResult] = useState<Awaited<ReturnType<typeof resolveWorkbench>>>()
  const [failure, setFailure] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let current = true
    resolveWorkbench({
      VITE_WORKBENCH_MODE: import.meta.env.VITE_WORKBENCH_MODE,
      VITE_WORKBENCH_API_BASE: import.meta.env.VITE_WORKBENCH_API_BASE,
      VITE_WORKBENCH_PROJECT_ID: import.meta.env.VITE_WORKBENCH_PROJECT_ID,
      VITE_WORKBENCH_PLAN_ID: import.meta.env.VITE_WORKBENCH_PLAN_ID,
    }).then((resolved) => {
      if (current) setResult(resolved)
    }, (error: unknown) => {
      if (current) setFailure(error instanceof WorkbenchApiError ? describeError(error)
        : error instanceof Error ? error.message : '无法连接 v2 服务。')
    })
    return () => { current = false }
  }, [attempt])
  if (failure) return <main className="loading-state"><p role="alert">{failure}</p>
    <button className="run-button" type="button" onClick={() => { setFailure(''); setAttempt((value) => value + 1) }}>重试连接</button>
  </main>
  if (!result) return <main className="loading-state" role="status">正在准备工作台…</main>
  return <>
    {result.message && <p className="loading-state" role="status">{result.message}</p>}
    <App api={result.api} />
  </>
}
