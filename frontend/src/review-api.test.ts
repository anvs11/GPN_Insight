import { describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { attachRequest, resolvedContext, reviewContext, reviewTarget } from './test-fixtures/review'
import { reviewItem } from './test-fixtures/import'

describe('Действующий HTTP Review', () => {
  it('использует POST resolve с точными полями и кодированием ID, без create/prompt', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...reviewItem, review_id: 'id/with?query' })))
    vi.stubGlobal('fetch', fetcher)
    await api.resolveReview('id/with?query', attachRequest)
    const [url, options] = fetcher.mock.calls[0]
    expect(url).toBe('/api/v1/reviews/id%2Fwith%3Fquery/resolve')
    expect(options.method).toBe('POST')
    expect(options.headers).toEqual({ Accept: 'application/json', 'Content-Type': 'application/json' })
    expect(JSON.parse(options.body)).toEqual(attachRequest)
  })
  it('читает Review, полную историю и полный каталог всех немерженных тем', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(reviewItem)))
      .mockResolvedValueOnce(new Response(JSON.stringify([reviewItem])))
      .mockResolvedValueOnce(new Response(JSON.stringify([reviewTarget])))
    vi.stubGlobal('fetch', fetcher)
    expect(await api.review(reviewItem.review_id)).toEqual(reviewItem)
    expect(await api.reviewHistory(reviewItem.review_id)).toEqual([reviewItem])
    expect(await api.topicCatalog()).toEqual([reviewTarget])
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/v1/reviews/synthetic-review', '/api/v1/reviews/synthetic-review/history', '/api/v1/topics?support_level=all'])
  })
  it.each([404, 409, 422, 503])('сохраняет настоящий HTTP %s и не повторяет POST', async status => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'SERVER_ERROR', message: 'Запрос отклонён.' }), { status }))
    vi.stubGlobal('fetch', fetcher)
    await expect(api.resolveReview(reviewItem.review_id, attachRequest)).rejects.toMatchObject({ status, code: 'SERVER_ERROR' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([{}, { ...reviewItem, review_id: 'other' }, { ...reviewItem, version: 0 }])('отклоняет чужой или повреждённый ответ %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(api.resolveReview(reviewItem.review_id, attachRequest)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it.each([[], [reviewItem, reviewItem], [{ ...reviewItem, review_id: 'other' }]].map(body => ({ body })))('отклоняет неполную историю %j', async ({ body }) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(api.reviewHistory(reviewItem.review_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it('принимает две версии одного ID в истории, хотя список очереди требует разные ID', async () => {
    const after = resolvedContext(attachRequest, 'a'.repeat(64), reviewContext)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(after.history))))
    expect(await api.reviewHistory(reviewItem.review_id)).toEqual(after.history)
  })
  it('отклоняет объединённую тему из каталога выбора', async () => {
    const target = structuredClone(reviewTarget); target.topic.status = 'merged'; target.topic.canonical_topic_id = 'canonical'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([target]))))
    await expect(api.topicCatalog()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('тайм-аут POST resolve объясняет неопределённость решения, а не предлагает новый запуск', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })))
    const outcome = expect(api.resolveReview(reviewItem.review_id, attachRequest)).rejects.toMatchObject({
      code: 'REQUEST_TIMEOUT', message: 'Сервер не ответил за 15 секунд. Решение Review могло сохраниться: проверьте Review и историю без повторной записи.',
    })
    await vi.advanceTimersByTimeAsync(15_000); await outcome
  })
})
