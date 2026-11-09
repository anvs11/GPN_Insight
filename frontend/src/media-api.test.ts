import { describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { importPreview, importResult } from './test-fixtures/import'
import { processingSource, processingDetail } from './test-fixtures/processing'
import type { MaterialInput } from './api-types'
import { readMediaManifest } from './media-manifest'

const asset: MaterialInput = { asset_id: 'synthetic', path: 'private-original.png', source_url: null, kind: 'image', mime_type: 'image/png' }
const source = { ...processingSource, ticket: { ...processingSource.ticket, assets: [asset] } }

describe('Медиа HTTP и граница snapshot', () => {
  it('передаёт raw JSON и выбранные файлы в повторяющихся files без ручного Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(importPreview))).mockResolvedValueOnce(new Response(JSON.stringify(importResult)))
    vi.stubGlobal('fetch', fetchMock)
    const raw = '{"synthetic":true,"synthetic":false}', files = [new File(['synthetic-bytes'], 'a.png')]
    await api.previewImport(raw, undefined, files)
    await api.commitImport(raw, undefined, files)
    for (const [, options] of fetchMock.mock.calls) {
      const body = options.body as FormData
      expect(body.get('manifest')).toBe(raw)
      expect((body.getAll('files')[0] as File).name).toBe('a.png')
      expect((body.getAll('files')[0] as File).size).toBe(files[0].size)
      expect(options.headers).not.toHaveProperty('Content-Type')
    }
  })
  it('читает зарегистрированный файл по IDs/version, отвергает HTML и не использует path', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('<html>not an image</html>', { headers: { 'Content-Type': 'text/html' } }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.media(source, asset)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/sources/${source.ticket.source_id}/versions/1/assets/synthetic`)
    expect(fetchMock.mock.calls[0][0]).not.toContain(asset.path)
  })
  it.each([403, 404, 503])('сохраняет безопасную HTTP %s ошибку выдачи медиа', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'ASSET_UNAVAILABLE', message: 'Файл недоступен.' }), { status })))
    await expect(api.media(source, asset)).rejects.toMatchObject({ status, code: 'ASSET_UNAVAILABLE' })
  })
  it('не обращается за несинтетическим оригиналом', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock)
    await expect(api.media({ ...source, synthetic: false }, asset)).rejects.toMatchObject({ status: 403 })
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it('останавливает загрузку при превышении фактического лимита', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(5 * 1048576 + 1)) }, cancel })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { headers: { 'Content-Type': 'image/png' } })))
    await expect(api.media(source, asset)).rejects.toMatchObject({ code: 'DOWNLOAD_TOO_LARGE' })
    expect(cancel).toHaveBeenCalled()
  })
  it('не принимает неверный UTF-8 отчёт', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'text/markdown; charset=utf-8' } })))
    await expect(api.weeklyReport()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it('отвергает новый processing ответ, не совпадающий с прочитанной версией', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(processingDetail))))
    await expect(api.processing({ ...processingSource, source_version: 2 })).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
  it('проверяет состав файлов, не пытаясь исправлять исходный JSON', () => {
    const raw = JSON.stringify({ synthetic: true, dataset_id: 'synthetic', tickets: [{ source_id: 's', created_at: '2026-11-02T12:00:00Z', text: '', assets: [{ ...asset, path: 'a.png' }] }] })
    expect(readMediaManifest(raw, [new File(['x'], 'a.png')]).sourceIds).toEqual(['s'])
    expect(readMediaManifest(raw.replace('"text":"",', ''), [new File(['x'], 'a.png')]).sourceIds).toEqual(['s'])
    expect(() => readMediaManifest(raw, [])).toThrow('Выберите все файлы')
    expect(() => readMediaManifest(raw, [new File(['x'], 'other.png')])).toThrow('лишний файл')
    expect(() => readMediaManifest(raw.replace('a.png', '../a.png'), [new File(['x'], 'a.png')])).toThrow('простое имя')
  })
})
