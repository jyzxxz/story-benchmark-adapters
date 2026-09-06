export interface SafeErrorSummary {
  status?: number
  code?: string
}

/**
 * Produces a log-safe error summary without Axios config, headers, request,
 * response data, or other objects that may retain a BYOK secret.
 */
export function getSafeErrorSummary(error: unknown): SafeErrorSummary {
  const candidate = error as {
    code?: unknown
    response?: { status?: unknown }
  } | null

  const status = candidate?.response?.status
  const code = candidate?.code

  return {
    status: typeof status === 'number' ? status : undefined,
    code: typeof code === 'string' ? code : undefined
  }
}
