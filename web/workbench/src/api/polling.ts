const maximumBackoff = 30_000
const maximumRetryExponent = 5

export function startPolling({
  interval, poll, onError,
}: {
  interval: number
  poll: (recovering: boolean, isCurrent: () => boolean) => Promise<boolean | 'done' | void>
  onError: (error: unknown) => void
}) {
  let timer: ReturnType<typeof setTimeout>
  let stopped = false
  let offline = navigator.onLine === false
  let running = false
  let recover = offline
  let wake = false
  let generation = 0
  let failures = 0

  function schedule(delay: number) {
    clearTimeout(timer)
    if (!stopped && !offline) timer = setTimeout(tick, delay)
  }

  async function tick() {
    if (stopped || offline || running) return
    running = true
    wake = false
    const attempt = generation
    const isCurrent = () => !stopped && attempt === generation
    try {
      const result = await poll(recover, isCurrent)
      if (result === 'done' && isCurrent()) stop()
      if (result !== false && isCurrent() && !wake) {
        failures = 0
        recover = false
      }
    } catch (error) {
      if (isCurrent()) {
        failures = Math.min(failures + 1, maximumRetryExponent)
        recover = true
        onError(error)
      }
    } finally {
      running = false
      const delay = failures === maximumRetryExponent ? maximumBackoff
        : Math.min(interval * 2 ** failures, maximumBackoff)
      schedule(wake ? 0 : delay)
    }
  }

  function disconnect() {
    generation += 1
    offline = true
    recover = true
    clearTimeout(timer)
    onError(new Error('网络已断开，等待重新连接。'))
  }

  function reconnect() {
    offline = false
    recover = true
    wake = running
    schedule(0)
  }

  window.addEventListener('offline', disconnect)
  window.addEventListener('online', reconnect)
  if (offline) disconnect()
  else schedule(interval)

  function stop() {
    stopped = true
    clearTimeout(timer)
    window.removeEventListener('offline', disconnect)
    window.removeEventListener('online', reconnect)
  }
  return stop
}
