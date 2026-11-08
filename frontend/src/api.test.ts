import { passportChange, passportDetail, passportRequest, savedPassport } from './test-fixtures/passport'
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

describe('Фильтры тем и статистика периода', () => {
  const period = { ...topicDetail.stats, ticket_count: 1, requester_count: null,
    date_from: '2026-11-01', date_to: '2026-11-01' }

  it('передаёт только поддерживаемые query-параметры, без режима и тела', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([{ ...topicListItem, period_stats: period }])))
    vi.stubGlobal('fetch', fetchMock)
    const filters = { status: 'needs_research' as const, support_level: 'all' as const,
      date_from: period.date_from, date_to: period.date_to }
    const result = await api.topics(undefined, filters)
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost')
    expect(Object.fromEntries(url.searchParams)).toEqual(filters)
    expect(result[0].stats.ticket_count).toBe(4)
    expect(result[0].period_stats?.ticket_count).toBe(1)
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined()
  })

  it.each(['date_from', 'date_to'] as const)('поддерживает одну границу %s', async field => {
    const bounds = { date_from: null, date_to: null, [field]: '2026-11-01' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{ ...topicListItem, period_stats: { ...period, ...bounds } }]))))
    const result = await api.topics(undefined, { [field]: '2026-11-01' })
    expect(result[0].period_stats?.[field]).toBe('2026-11-01')
  })

  it.each([
    null,
    { ...period, ticket_count: -1 },
    { ...period, requester_count: 'unknown' },
    { ...period, date_from: '2026-10-01' },
    { ...period, weekly_counts: null },
  ])('не публикует неверную или чужую статистику периода: %j', async period_stats => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{ ...topicListItem, period_stats }]))))
    await expect(api.topics(undefined, { date_from: '2026-11-01', date_to: '2026-11-01' })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('отклоняет неожиданную статистику периода при запросе за всё время', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([{ ...topicListItem, period_stats: period }]))))
    await expect(api.topics()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('показывает серверный 422 без замены пустым списком', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'INVALID_PERIOD', message: 'Неверный период.' }), { status: 422 })))
    await expect(api.topics(undefined, { date_from: '2026-11-02', date_to: '2026-11-01' })).rejects.toMatchObject({ status: 422, code: 'INVALID_PERIOD' })
  })
})

describe('Паспорт темы: реальный формат HTTP', () => {
  it('отправляет два номера версии и точное тело PATCH паспорта', async () => {
    const request = passportRequest(), saved = savedPassport(passportDetail, request)
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(saved)))
    vi.stubGlobal('fetch', fetchMock)
    expect(await api.savePassport(passportDetail.topic.topic_id, request)).toEqual(saved)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/topics/' + passportDetail.topic.topic_id + '/passport')
    expect(fetchMock.mock.calls[0][1].method).toBe('PATCH')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(request)
  })
  it('читает отдельную историю и отклоняет чужую или повреждённую запись', async () => {
    const request = passportRequest(), event = passportChange(passportDetail, request)
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([event])))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ ...event, topic_id: 'other' }])))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ ...event, after: {} }])))
      .mockResolvedValueOnce(new Response(JSON.stringify([event, event])))
    vi.stubGlobal('fetch', fetchMock)
    expect(await api.topicChanges(passportDetail.topic.topic_id)).toEqual([event])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/topics/' + passportDetail.topic.topic_id + '/changes')
    for (let i = 0; i < 3; i++) await expect(api.topicChanges(passportDetail.topic.topic_id)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it.each([404, 409, 422, 503])('сохраняет HTTP %s без имитации успеха и повторного PATCH', async status => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'SERVER_ERROR', message: 'Не сохранено.' }), { status }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.savePassport(passportDetail.topic.topic_id, passportRequest())).rejects.toMatchObject({ status })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
