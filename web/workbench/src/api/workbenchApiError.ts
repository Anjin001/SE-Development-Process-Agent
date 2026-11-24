export class WorkbenchApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Readonly<Record<string, unknown>>

  constructor(message: string, code: string, status: number, details: Readonly<Record<string, unknown>> = {}) {
    super(message)
    this.name = 'WorkbenchApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}
