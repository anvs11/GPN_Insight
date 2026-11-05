import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

describe('HTTP-контракт обработки', () => {
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
