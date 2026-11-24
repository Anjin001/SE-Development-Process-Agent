// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { startPolling } from './polling'

afterEach(() => vi.useRealTimers())

it('限制快速重试并在恢复后回到正常轮询间隔', async () => {
  vi.useFakeTimers()
  const poll = vi.fn<Parameters<typeof startPolling>[0]['poll']>()
    .mockRejectedValue(new Error('离线服务'))
  const onError = vi.fn()
  const stop = startPolling({ interval: 100, poll, onError })
  try {
    await vi.advanceTimersByTimeAsync(3100)
    expect(poll).toHaveBeenCalledTimes(5)
    expect(onError).toHaveBeenCalledTimes(5)
    await vi.advanceTimersByTimeAsync(29_999)
    expect(poll).toHaveBeenCalledTimes(5)
    poll.mockResolvedValue(undefined)
    await vi.advanceTimersByTimeAsync(1)
    expect(poll).toHaveBeenCalledTimes(6)
    expect(poll.mock.calls.at(-1)?.[0]).toBe(true)
    await vi.advanceTimersByTimeAsync(100)
    expect(poll.mock.calls.at(-1)?.[0]).toBe(false)
    expect(poll).toHaveBeenCalledTimes(7)
  } finally { stop() }
})

it('离线暂停并废弃正在进行的读取，重新联网只唤醒一次', async () => {
  vi.useFakeTimers()
  let finish!: () => void
  const poll = vi.fn<Parameters<typeof startPolling>[0]['poll']>()
    .mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve }))
    .mockResolvedValue(undefined)
  const stop = startPolling({ interval: 100, poll, onError: vi.fn() })
  try {
    await vi.advanceTimersByTimeAsync(100)
    window.dispatchEvent(new Event('offline'))
    expect(poll.mock.calls[0][1]()).toBe(false)
    await vi.advanceTimersByTimeAsync(500)
    expect(poll).toHaveBeenCalledTimes(1)
    window.dispatchEvent(new Event('online'))
    window.dispatchEvent(new Event('online'))
    finish()
    await vi.advanceTimersByTimeAsync(1)
    expect(poll).toHaveBeenCalledTimes(2)
    expect(poll.mock.calls[1][0]).toBe(true)
  } finally { stop() }
  window.dispatchEvent(new Event('online'))
  await vi.advanceTimersByTimeAsync(1000)
  expect(poll).toHaveBeenCalledTimes(2)
})
