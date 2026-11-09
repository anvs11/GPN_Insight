import { describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { readTextManifest, textManifest } from './import-manifest'
import { importEntry, importPreview, importResult, reviewItem } from './test-fixtures/import'

const draft = { source_id: 'synthetic-source', created_at: '2026-10-09T12:00:00+03:00', text: 'Синтетический текст', requester_id: '' }
const raw = textManifest('synthetic-frontend-import', draft)
describe('Контракты текстового импорта и review', () => {
  it('использует отдельные multipart запросы с исходным JSON без ручного Content-Type', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(importPreview)))
      .mockResolvedValueOnce(new Response(JSON.stringify(importResult)))
    vi.stubGlobal('fetch', fetcher)
    await api.previewImport(raw); await api.commitImport(raw)
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/v1/imports?dry_run=true', '/api/v1/imports'])
    const requests = fetcher.mock.calls.map(([, options]) => options as RequestInit)
    for (const request of requests) {
      expect(request.method).toBe('POST')
      expect(request.headers).toEqual({ Accept: 'application/json' })
      expect(request.body).toBeInstanceOf(FormData)
      expect((request.body as FormData).get('manifest')).toBe(raw)
    }
    expect(requests[0].body).not.toBe(requests[1].body)
  })
  it.each([409, 413, 422, 503])('передаёт HTTP %s и безопасное сообщение без повтора', async status => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'IMPORT_ERROR', message: 'Набор отклонён.' }), { status }))
    vi.stubGlobal('fetch', fetcher)
    await expect(api.commitImport(raw)).rejects.toMatchObject({ status, code: 'IMPORT_ERROR' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([
    { ...importPreview, can_commit: false },
    { ...importPreview, counts: { ...importPreview.counts, invalid: 1 } },
    { ...importPreview, errors: null },
    { ...importPreview, counts: { ...importPreview.counts, inserted: -1 } },
    { ...importPreview, dataset_id: null },
  ])('не разрешает импорт по повреждённому preview %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(api.previewImport(raw)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it('не принимает preview вместо квитанции commit', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(importPreview))))
    await expect(api.commitImport(raw)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it('читает настоящий журнал и очередь с единственным поддерживаемым фильтром', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([importEntry])))
      .mockResolvedValueOnce(new Response(JSON.stringify([reviewItem])))
    vi.stubGlobal('fetch', fetcher)
    expect(await api.imports()).toEqual([importEntry])
    expect(await api.reviews()).toEqual([reviewItem])
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/v1/imports', '/api/v1/reviews?status=all'])
  })
  it.each([[importEntry, importEntry], [{ ...importEntry, synthetic: 'true' }], [{ ...importEntry, result: { ...importResult, counts: { invalid: 1 } } }]].map(body => ({ body })))(
    'отклоняет неоднозначный или повреждённый журнал %j', async ({ body }) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
      await expect(api.imports()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    })
  it.each([
    { ...reviewItem, resolution: {} }, { ...reviewItem, status: 'unknown' },
    { ...reviewItem, need_ids: [] }, { ...reviewItem, version: 0 }, { ...reviewItem, evidence_refs: null },
  ])('отклоняет повреждённый review %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify([body]))))
    await expect(api.reviews()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it('передаёт отмену запроса чтения, не превращая её в результат', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const outcome = expect(api.reviews(controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort(); await outcome
  })
})

describe('Ограничения synthetic text-only формы', () => {
  it('строит настоящий manifest, не придумывая ручные model/prompt-поля', () => {
    expect(JSON.parse(raw)).toEqual({ dataset_id: 'synthetic-frontend-import', synthetic: true, tickets: [{
      ...draft, product_id: 'webneft', requester_id: null, assets: [],
    }] })
    expect(readTextManifest(raw).tickets[0].text).toBe(draft.text)
  })
  it.each([
    '{}', '{"synthetic":false}', '{not json}',
    JSON.stringify({ dataset_id: 'test', synthetic: true, tickets: [{ ...draft, text: '  ' }] }),
    JSON.stringify({ dataset_id: 'test', synthetic: true, tickets: [{ ...draft, assets: [{ path: 'a.wav' }] }] }),
    JSON.stringify({ dataset_id: 'test', synthetic: true, tickets: [{ ...draft, product_id: 'other' }] }),
    JSON.stringify({ dataset_id: 'test', synthetic: true, tickets: Array.from({ length: 101 }, () => draft) }),
  ])('отклоняет неподдерживаемый ввод %s', value => {
    expect(() => readTextManifest(value)).toThrow()
  })
  it('ограничивает именно UTF-8 байты, а не число символов', () => {
    expect(() => readTextManifest(textManifest('test', { ...draft, text: 'я'.repeat(524_288) }))).toThrow('1 МиБ')
  })
  it('не исправляет повторный JSON ключ: исходный текст останется серверному валидатору', () => {
    const repeated = raw.replace('"synthetic":true', '"synthetic":true,"synthetic":true')
    expect(readTextManifest(repeated).tickets).toHaveLength(1)
    expect(repeated).toContain('"synthetic":true,"synthetic":true')
  })
})
