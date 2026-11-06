import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'
import { topicListItem } from './test-fixtures/topic'
import { sourceDetail, topicDetail } from './test-fixtures/detail'

describe('HTTP-контракт обработки', () => {
  it('объясняет тайм-аут PATCH как неопределённый результат записи, без предложения повторить запуск', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })))
    const outcome = expect(api.saveDecision(topicDetail.topic.topic_id, { status: 'new', owner: 'Тест', reason: 'Проверка', version: 2 }))
      .rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', message: 'Сервер не ответил за 15 секунд. Решение могло сохраниться: проверьте карточку перед повторной отправкой.' })
    await vi.advanceTimersByTimeAsync(15_000)
    await outcome
  })
  it('передаёт только поддерживаемые поля PATCH с целой версией и кодирует ID темы', async () => {
    const id = 'synthetic/id?x=1'
    const body = { status: 'needs_research' as const, owner: 'Тестовый владелец', reason: 'Синтетическая проверка', version: 1 }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...topicDetail, topic: { ...topicDetail.topic, topic_id: id } })))
    vi.stubGlobal('fetch', fetchMock)
    await api.saveDecision(id, body)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/topics/synthetic%2Fid%3Fx%3D1/decision')
    const options: RequestInit = fetchMock.mock.calls[0][1]
    expect(options.method).toBe('PATCH')
    expect(options.headers).toEqual({ Accept: 'application/json', 'Content-Type': 'application/json' })
    expect(JSON.parse(String(options.body))).toEqual(body)
  })

  it.each([404, 409, 422, 503])('не скрывает HTTP %s при PATCH', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'SERVER_ERROR', message: 'Сервер отклонил решение.' }), { status })))
    await expect(api.saveDecision(topicDetail.topic.topic_id, { status: 'new', owner: 'Тест', reason: 'Проверка', version: 2 })).rejects.toMatchObject({ status, code: 'SERVER_ERROR' })
  })

  it.each([{}, { ...topicDetail, topic: { ...topicDetail.topic, topic_id: 'another-topic' } }])('отклоняет неподходящий ответ PATCH: %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(api.saveDecision(topicDetail.topic.topic_id, { status: 'new', owner: 'Тест', reason: 'Проверка', version: 2 })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('читает подробную карточку и текущий источник через GET с кодированием ID', async () => {
    const id = 'synthetic/id?x=1'
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ ...topicDetail, topic: { ...topicDetail.topic, topic_id: id } })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...sourceDetail, ticket: { ...sourceDetail.ticket, source_id: id }, document: null })))
    vi.stubGlobal('fetch', fetchMock)
    expect((await api.topic(id)).topic.version).toBe(2)
    expect((await api.source(id)).source_version).toBe(1)
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual(['/api/v1/topics/synthetic%2Fid%3Fx%3D1', '/api/v1/sources/synthetic%2Fid%3Fx%3D1'])
    for (const [, options] of fetchMock.mock.calls) {
      expect(options.method).toBeUndefined()
      expect(options.body).toBeUndefined()
      expect(options.cache).toBe('no-store')
    }
  })

  it.each([
    { ...topicDetail, topic: { ...topicDetail.topic, version: 0 } },
    { ...topicDetail, topic: { ...topicDetail.topic, topic_id: 'другая-тема' } },
    { ...topicDetail, sources: [{ ...sourceDetail, document: { fragments: null } }] },
    { ...topicDetail, stats: { ...topicDetail.stats, first_seen: 'не дата' } },
  ])('не публикует повреждённую карточку или ответ другой темы: %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(api.topic(topicDetail.topic.topic_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it.each([
    { ...sourceDetail, ticket: { ...sourceDetail.ticket, source_id: 'другой-источник' }, document: null },
    { ...sourceDetail, needs: null },
    { ...sourceDetail, document: { ...sourceDetail.document, errors: null } },
  ])('отклоняет повреждённый источник или ответ другого обращения: %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(api.source(sourceDetail.ticket.source_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it.each(['topic', 'source'] as const)('сохраняет настоящий HTTP 404 при чтении %s', async resource => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'NOT_FOUND', message: 'Запись не найдена.' }), { status: 404 })))
    await expect(api[resource]('missing')).rejects.toMatchObject({ status: 404, code: 'NOT_FOUND' })
  })

  it('читает сохранённые темы без выдуманных фильтров и сохраняет null заявителей', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([topicListItem])))
    vi.stubGlobal('fetch', fetchMock)
    expect(await api.topics()).toEqual([topicListItem])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/topics')
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined()
  })

  it.each([null, {}, [{ topic: { title: 'Неполный ответ' } }], [{ ...topicListItem, stats: { ...topicListItem.stats, ticket_count: -1 } }]])(
    'отклоняет неверный ответ списка вместо публикации ложного пустого результата: %j', async body => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
      await expect(api.topics()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    })

  it('передаёт период без клиентского режима и использует относительный API', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"run_id":"test"}', { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.startRun({ product_id: 'webneft', date_from: '2026-10-01', date_to: null })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/runs')
    const options: RequestInit = fetchMock.mock.calls[0][1]
    expect(options.method).toBe('POST')
    expect(JSON.parse(String(options.body))).toEqual({ product_id: 'webneft', date_from: '2026-10-01', date_to: null })
  })

  it('сохраняет код и сообщение 503, не возвращая фиктивный Run', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'INTEGRATION_NOT_READY', message: 'Интеграция не завершена. Запуск не создан.',
    }), { status: 503 })))
    await expect(api.startRun({ product_id: 'webneft' })).rejects.toMatchObject({
      status: 503, code: 'INTEGRATION_NOT_READY', message: 'Интеграция не завершена. Запуск не создан.',
    })
  })

  it('объясняет HTML вместо JSON как ошибку подключения API', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Vite</html>')))
    await expect(api.health()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('сообщает о потере соединения', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.health()).rejects.toBeInstanceOf(ApiError)
    await expect(api.health()).rejects.toMatchObject({ code: 'NETWORK_UNAVAILABLE', status: 0 })
  })

  it('кодирует ID запуска как один сегмент URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    await api.run('run/id?x=1')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/runs/run%2Fid%3Fx%3D1')
  })

  it('не выдаёт тайм-аут чтения тела ответа за неизвестный формат JSON', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => ({
      status: 200, ok: true,
      json: () => new Promise((_resolve, reject) => {
        options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      }),
    })))
    const result = expect(api.health()).rejects.toMatchObject({ status: 0, code: 'REQUEST_TIMEOUT' })
    await vi.advanceTimersByTimeAsync(15_000)
    await result
  })

  it('сохраняет отмену запроса пользователем, не превращая её в сетевой отказ', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => ({
      status: 200, ok: true,
      json: () => new Promise((_resolve, reject) => {
        options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      }),
    })))
    const controller = new AbortController()
    const result = expect(api.health(controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    await Promise.resolve()
    controller.abort()
    await result
  })
})
