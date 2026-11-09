import type { MaterialInput, ResolveReviewRequest, ImportLogEntry, ImportPreview, ImportResult, ReviewItem, ApiErrorBody, DecisionUpdate, Health, PassportUpdate, Run, RunRequest, SourceDetail, TopicChangeEvent, TopicDetail, TopicFilters, TopicListItem } from './api-types'
import { isImportLog, isImportPreview, isImportResult, isReviews, isSourceDetail, isTopicChanges, isTopicDetail, isTopicListItem } from './api-validators'
import { hasValidRunCoverage, isAssessmentSummary, isCompletedWeek, isProcessingDetail } from './processing-contracts'
import type { AssessmentSummary, CompletedWeek, ProcessingDetail } from './processing-contracts'

// U4 использует только same-origin идентификаторы, никогда путь/URL из manifest.
export function sourceMediaUrl(sourceId: string, sourceVersion: number, assetId: string): string {
  if (!Number.isSafeInteger(sourceVersion) || sourceVersion < 1 || !sourceId || !assetId) throw new Error('Нужны точные идентификаторы и версия источника.')
  return `/api/v1/sources/${encodeURIComponent(sourceId)}/versions/${sourceVersion}/assets/${encodeURIComponent(assetId)}`
}

export const weeklyReportUrl = '/api/v1/reports/weekly'

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
        ? path.endsWith('/resolve')
          ? 'Сервер не ответил за 15 секунд. Решение Review могло сохраниться: проверьте Review и историю без повторной записи.'
          : init.method === 'PATCH'
          ? 'Сервер не ответил за 15 секунд. Решение могло сохраниться: проверьте карточку перед повторной отправкой.'
          : 'Сервер не ответил за 15 секунд. Запрос мог быть принят: проверьте состояние перед повторным запуском.'
        : 'Не удалось связаться с сервером. Проверьте, что Backend запущен на 127.0.0.1:8000.',
    })
  } finally {
    window.clearTimeout(timeout)
    init.signal?.removeEventListener('abort', cancel)
  }
}

function importBody(manifest: string, files: readonly File[] = []) {
  const form = new FormData()
  form.append('manifest', manifest)
  for (const file of files) form.append('files', file, file.name)
  return form
}

async function download(path: string, mime: string, limit: number, signal?: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  signal?.addEventListener('abort', cancel, { once: true })
  if (signal?.aborted) cancel()
  const timeout = window.setTimeout(cancel, 30_000)
  try {
    const response = await fetch(path, { signal: controller.signal, cache: 'no-store', headers: { Accept: mime } })
    if (!response.ok) {
      const value: unknown = await response.json().catch(() => null)
      throw new ApiError(response.status, isErrorBody(value) ? value : { code: 'HTTP_ERROR', message: 'Сервер не выдал файл. Проверьте доступ и повторите чтение.' })
    }
    if (response.status !== 200 || response.headers.get('content-type')?.split(';')[0].trim() !== mime || !response.body) {
      throw new ApiError(response.status, { code: 'INVALID_RESPONSE', message: 'Тип или содержимое файла не соответствует запросу. Просмотр заблокирован.' })
    }
    const reader = response.body.getReader(), chunks: Uint8Array<ArrayBuffer>[] = []
    let size = 0
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        size += value.byteLength
        if (size > limit) throw new ApiError(413, { code: 'DOWNLOAD_TOO_LARGE', message: 'Ответ превышает допустимый размер. Загрузка остановлена.' })
        chunks.push(new Uint8Array(value))
      }
    } catch (error) { await reader.cancel().catch(() => {}); throw error }
    finally { reader.releaseLock() }
    if (!size) throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Получен пустой файл; результат не подтверждён.' })
    const bytes = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return bytes
  } catch (error) {
    if (signal?.aborted || error instanceof ApiError) throw error
    throw new ApiError(0, { code: controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'NETWORK_UNAVAILABLE',
      message: 'Файл не получен. Проверьте соединение и повторите чтение.' })
  } finally { window.clearTimeout(timeout); signal?.removeEventListener('abort', cancel) }
}

function isReview(value: unknown): value is ReviewItem { return isReviews([value]) }
function checkedRunCoverage(run: Run): Run {
  if (!hasValidRunCoverage(run)) throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Счётчики источников и материалов не соответствуют контракту. Обновите запуск.' })
  return run
}

export const api = {
  processing: async (source: SourceDetail, signal?: AbortSignal): Promise<ProcessingDetail> => {
    const result = await request<unknown>(`/sources/${encodeURIComponent(source.ticket.source_id)}/processing?source_version=${source.source_version}`, { signal })
    if (!isProcessingDetail(result, source)) throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Ревизия обработки изменилась или ответ неполный. Обновите источник; прежние данные сохранены.' })
    return result
  },
  assessmentSummary: async (card: TopicDetail, signal?: AbortSignal): Promise<AssessmentSummary> => {
    const result = await request<unknown>(`/topics/${encodeURIComponent(card.topic.topic_id)}/assessment-summary`, { signal })
    if (!isAssessmentSummary(result, card)) throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Версия оценки не совпала с карточкой. Обновите карточку и повторите чтение.' })
    return result
  },
  weeklyPeriod: async (signal?: AbortSignal): Promise<CompletedWeek> => {
    const result = await request<unknown>('/reports/weekly/period', { signal })
    if (!isCompletedWeek(result)) throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Сервер вернул некорректный период завершённой недели. Повторите чтение.' })
    return result
  },
  weeklyReport: async (signal?: AbortSignal): Promise<string> => {
    const raw = await download('/api/v1/reports/weekly', 'text/markdown', 2 * 1048576, signal)
    try { return new TextDecoder('utf-8', { fatal: true }).decode(raw) }
    catch { throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Отчёт не является корректным UTF-8. Скачивание не подтверждено.' }) }
  },
  media: async (source: SourceDetail, asset: MaterialInput, signal?: AbortSignal): Promise<Blob> => {
    if (!source.synthetic || source.original_access === 'restricted' || !source.ticket.assets.some(a => a.asset_id === asset.asset_id && a.mime_type === asset.mime_type)) {
      return Promise.reject(new ApiError(403, { code: 'ORIGINAL_ACCESS_RESTRICTED', message: 'Этот оригинал недоступен. Допустимы только зарегистрированные синтетические файлы.' }))
    }
    const raw = await download(sourceMediaUrl(source.ticket.source_id, source.source_version, asset.asset_id), asset.mime_type,
      (asset.kind === 'audio' ? 10 : 5) * 1048576, signal)
    return new Blob([raw.buffer], { type: asset.mime_type })
  },
  review: async (id: string, signal?: AbortSignal): Promise<ReviewItem> => {
    const result = await request<unknown>('/reviews/' + encodeURIComponent(id), { signal })
    if (!isReview(result) || result.review_id !== id) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Review не соответствует контракту или выбранному ID. Повторите чтение.',
    })
    return result
  },
  reviewHistory: async (id: string, signal?: AbortSignal): Promise<ReviewItem[]> => {
    const result = await request<unknown>('/reviews/' + encodeURIComponent(id) + '/history', { signal })
    if (!Array.isArray(result) || result.length === 0 ||
      !result.every((entry: unknown, index): entry is ReviewItem => isReview(entry) &&
        entry.review_id === id && entry.version === index + 1)) {
      throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'История Review неполна или не соответствует контракту. Сохранение не подтверждено.' })
    }
    return result
  },
  resolveReview: async (id: string, body: ResolveReviewRequest, signal?: AbortSignal): Promise<ReviewItem> => {
    const result = await request<unknown>('/reviews/' + encodeURIComponent(id) + '/resolve', {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!isReview(result) || result.review_id !== id) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Ответ решения Review не соответствует контракту. Проверьте результат чтением без повторной записи.',
    })
    return result
  },
  topicCatalog: async (signal?: AbortSignal): Promise<TopicDetail[]> => {
    // Нужны полные карточки, включая одиночные темы и темы с нулём активных связей.
    const result = await request<unknown>('/topics?support_level=all', { signal })
    if (!Array.isArray(result) || !result.every(isTopicDetail) || result.some(item =>
      item.period_stats !== null || item.topic.status === 'merged' || item.topic.passport.product_id !== 'webneft') ||
      new Set(result.map(item => item.topic.topic_id)).size !== result.length) {
      throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Каталог тем не соответствует контракту. Решение Review недоступно.' })
    }
    return result
  },
  previewImport: async (manifest: string, signal?: AbortSignal, files: readonly File[] = []): Promise<ImportPreview> => {
    const result = await request<unknown>('/imports?dry_run=true', { method: 'POST', body: importBody(manifest, files), signal })
    if (!isImportPreview(result)) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Предпросмотр не соответствует контракту. Подтверждение импорта недоступно.',
    })
    return result
  },
  commitImport: async (manifest: string, signal?: AbortSignal, files: readonly File[] = []): Promise<ImportResult> => {
    const result = await request<unknown>('/imports', { method: 'POST', body: importBody(manifest, files), signal })
    if (!isImportResult(result)) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Ответ импорта не соответствует контракту. Запись могла пройти: проверьте журнал без повторной отправки.',
    })
    return result
  },
  imports: async (signal?: AbortSignal): Promise<ImportLogEntry[]> => {
    const result = await request<unknown>('/imports', { signal })
    if (!isImportLog(result)) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Журнал импортов не соответствует контракту. Результат не подтверждён.',
    })
    return result
  },
  reviews: async (signal?: AbortSignal): Promise<ReviewItem[]> => {
    const result = await request<unknown>('/reviews?status=all', { signal })
    if (!isReviews(result)) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Очередь проверки не соответствует контракту. Повторите чтение.',
    })
    return result
  },
  savePassport: async (id: string, body: PassportUpdate, signal?: AbortSignal): Promise<TopicDetail> => {
    const result = await request<unknown>('/topics/' + encodeURIComponent(id) + '/passport', {
      method: 'PATCH', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!isTopicDetail(result) || result.topic.topic_id !== id) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'Ответ сохранения паспорта не соответствует контракту. Проверьте карточку и историю изменений.',
    })
    return result
  },
  topicChanges: async (id: string, signal?: AbortSignal): Promise<TopicChangeEvent[]> => {
    const result = await request<unknown>('/topics/' + encodeURIComponent(id) + '/changes', { signal })
    if (!isTopicChanges(result) || result.some(event => event.topic_id !== id)) throw new ApiError(200, {
      code: 'INVALID_RESPONSE', message: 'История изменений не соответствует контракту или выбранной теме. Повторите чтение.',
    })
    return result
  },
  saveDecision: async (id: string, body: DecisionUpdate, signal?: AbortSignal): Promise<TopicDetail> => {
    const result = await request<unknown>(`/topics/${encodeURIComponent(id)}/decision`, {
      method: 'PATCH', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!isTopicDetail(result) || result.topic.topic_id !== id) {
      throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Ответ сохранения не соответствует контракту или выбранной теме. Проверьте сохранение повторным чтением карточки.' })
    }
    return result
  },
  topic: async (id: string, signal?: AbortSignal): Promise<TopicDetail> => {
    const result = await request<unknown>(`/topics/${encodeURIComponent(id)}`, { signal })
    if (!isTopicDetail(result) || result.topic.topic_id !== id) {
      throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Ответ карточки не соответствует контракту или выбранной теме. Обновите данные и проверьте версию Backend.' })
    }
    return result
  },
  source: async (id: string, signal?: AbortSignal): Promise<SourceDetail> => {
    const result = await request<unknown>(`/sources/${encodeURIComponent(id)}`, { signal })
    if (!isSourceDetail(result) || result.ticket.source_id !== id) {
      throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Ответ источника не соответствует контракту или выбранному обращению. Обновите данные и проверьте версию Backend.' })
    }
    return result
  },
  topics: async (signal?: AbortSignal, filters: TopicFilters = {}): Promise<TopicListItem[]> => {
    const query = new URLSearchParams()
    for (const key of ['status', 'support_level', 'date_from', 'date_to'] as const) {
      const value = filters[key]
      if (value) query.set(key, value)
    }
    const result = await request<unknown>('/topics' + (query.size > 0 ? '?' + query.toString() : ''), { signal })
    const hasPeriod = Boolean(filters.date_from || filters.date_to)
    if (!Array.isArray(result) || !result.every(isTopicListItem) || result.some(item => hasPeriod
      ? item.period_stats === null || item.period_stats.date_from !== (filters.date_from || null) ||
        item.period_stats.date_to !== (filters.date_to || null)
      : item.period_stats !== null)) {
      throw new ApiError(200, { code: 'INVALID_RESPONSE',
        message: 'Ответ со списком тем не соответствует контракту. Проверьте версию Backend и повторите загрузку.' })
    }
    return result
  },
  health: (signal?: AbortSignal) => request<Health>('/health', { signal }),
  startRun: (body: RunRequest, signal?: AbortSignal) => request<Run>('/runs', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(checkedRunCoverage),
  run: (id: string, signal?: AbortSignal) => request<Run>(`/runs/${encodeURIComponent(id)}`, { signal }).then(checkedRunCoverage),
}
