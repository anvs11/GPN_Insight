import type { ApiErrorBody, Health, Run, RunRequest } from './api-types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === 'object' && value !== null &&
    'code' in value && typeof value.code === 'string' &&
    'message' in value && typeof value.message === 'string'
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  if (init.signal?.aborted) controller.abort()
  init.signal?.addEventListener('abort', cancel, { once: true })
  const timeout = window.setTimeout(cancel, 15_000)
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...init,
      signal: controller.signal,
      cache: 'no-store',
      headers: { Accept: 'application/json', ...init.headers },
    })
    let body: unknown
    try {
      body = await response.json()
    } catch (error) {
      if (controller.signal.aborted) throw error
      throw new ApiError(response.status, {
        code: 'INVALID_RESPONSE',
        message: 'Сервер вернул ответ в неизвестном формате. Проверьте запуск Backend и прокси /api.',
      })
    }
    if (!response.ok) {
      throw new ApiError(response.status, isErrorBody(body) ? body : {
        code: 'HTTP_ERROR', message: `Сервер отклонил запрос (HTTP ${response.status}). Повторите проверку позже.`,
      })
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (init.signal?.aborted) throw error
    throw new ApiError(0, {
      code: controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'NETWORK_UNAVAILABLE',
      message: controller.signal.aborted
        ? 'Сервер не ответил за 15 секунд. Запрос мог быть принят: проверьте состояние перед повторным запуском.'
        : 'Не удалось связаться с сервером. Проверьте, что Backend запущен на 127.0.0.1:8000.',
    })
  } finally {
    window.clearTimeout(timeout)
    init.signal?.removeEventListener('abort', cancel)
  }
}

export const api = {
  health: (signal?: AbortSignal) => request<Health>('/health', { signal }),
  startRun: (body: RunRequest, signal?: AbortSignal) => request<Run>('/runs', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  run: (id: string, signal?: AbortSignal) => request<Run>(`/runs/${encodeURIComponent(id)}`, { signal }),
}
