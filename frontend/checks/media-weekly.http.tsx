// Реальные HTTP сценарии в jsdom, не визуальная приёмка Windows браузера.
import { readFileSync } from 'node:fs'
import { Buffer } from 'node:buffer'
import process from 'node:process'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MediaImportPanel } from '../src/components/MediaImportPanel'
import { SourceViewer } from '../src/components/SourceViewer'
import { TopicDetail } from '../src/components/TopicDetail'
import { WeeklyReport } from '../src/components/WeeklyReport'
import { ProcessingPanel } from '../src/components/ProcessingPanel'

if (!process.env.GPN_UI_HTTP_FIXTURE) throw new Error('Use backend/scripts/check_frontend_media_http.py to start isolated server')
const fixture = JSON.parse(readFileSync(process.env.GPN_UI_HTTP_FIXTURE, 'utf8')) as {
  baseUrl: string; manifest: string; sourceId: string; topicId: string; files: { path: string; name: string; mime: string }[]
}
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(fixture.baseUrl)) throw new Error('Only local synthetic test server is allowed')
const nativeFetch = globalThis.fetch

function fileBytes(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => reader.result instanceof ArrayBuffer ? resolve(reader.result) : reject(new Error('Invalid test file'))
    reader.onerror = () => reject(new Error('Unreadable test file'))
    reader.readAsArrayBuffer(file)
  })
}
beforeEach(() => {
  // Node fetch не сериализует jsdom FormData как браузер; мост применяется
  // только в этом test transport. Production api.ts использует обычный FormData.
  vi.stubGlobal('fetch', async (input: string, init: RequestInit = {}) => {
    if (!input.startsWith('/api/v1/')) throw new Error('Unexpected non-API request')
    if (init.body instanceof FormData) {
      const boundary = 'SyntheticUiHttpBoundary'
      const chunks: Buffer[] = []
      for (const [name, value] of init.body.entries()) {
        chunks.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"`))
        if (typeof value === 'string') chunks.push(Buffer.from(`\r\n\r\n${value}\r\n`))
        else chunks.push(Buffer.from(`; filename="${value.name}"\r\nContent-Type: ${value.type || 'application/octet-stream'}\r\n\r\n`),
          Buffer.from(await fileBytes(value)), Buffer.from('\r\n'))
      }
      chunks.push(Buffer.from(`--${boundary}--\r\n`))
      return nativeFetch(fixture.baseUrl + input, { ...init, body: Buffer.concat(chunks), headers: {
        ...init.headers, 'Content-Type': `multipart/form-data; boundary=${boundary}`,
      } })
    }
    return nativeFetch(fixture.baseUrl + input, init)
  })
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:synthetic-http-check'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})

describe('Synthetic frontend -> HTTP -> SQLite -> frontend', () => {
  it('выполняет preview/commit/repeat медиа без дублей', async () => {
    render(<MediaImportPanel />)
    const raw = readFileSync(fixture.manifest, 'utf8')
    const manifest = new File([raw], 'manifest.json', { type: 'application/json' })
    Object.defineProperty(manifest, 'arrayBuffer', { value: async () => new TextEncoder().encode(raw).buffer })
    fireEvent.change(screen.getByLabelText('JSON manifest для медиа'), { target: { files: [manifest] } })
    fireEvent.change(screen.getByLabelText('Файлы WAV, MP3, PNG, JPEG'), { target: { files: fixture.files.map(file =>
      new File([new Uint8Array(readFileSync(file.path))], file.name, { type: file.mime })) } })
    await screen.findByText(/JSON: manifest.json/)
    for (let i = 0; i < 2; i++) {
      fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр медиа' }))
      await screen.findByRole('button', { name: 'Подтвердить импорт медиа' })
      expect(screen.getByText(/Новых: 0; обновлений: 0; без изменений: 1/)).toBeTruthy()
      fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт медиа' }))
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Подтвердить импорт медиа' })).toBeNull())
      await screen.findByRole('region', { name: 'Подтверждённый медиаимпорт' })
    }
    const saved = await (await nativeFetch(fixture.baseUrl + '/api/v1/sources/' + fixture.sourceId)).json()
    expect(saved.source_version).toBe(1)
  })
  it('читает partial/errors/unknown и явно загружает PNG/аудио', async () => {
    render(<SourceViewer sourceId={fixture.sourceId} onClose={() => {}} />)
    await screen.findByText(/Результат: частичное покрытие/)
    expect(screen.getAllByText(/Приоритет: Недостаточно данных/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/REPLAY_RECORD_MISSING/).length + screen.queryAllByText(/REGISTERED_MATERIAL_INTEGRITY_FAILED/).length).toBeGreaterThan(0)
    // Восстановленный файл уже доступен, хотя сохранённый анализ пока partial.
    fireEvent.click(screen.getByRole('button', { name: 'Открыть медиа fixture-screen-png' }))
    await screen.findByRole('img')
    fireEvent.click(screen.getByRole('button', { name: 'Открыть медиа fixture-call-wav' }))
    await screen.findByLabelText('Аудио fixture-call-wav')
  })
  it('читает summary и отчёт с реального сервера', async () => {
    const view = render(<TopicDetail topicId={fixture.topicId} onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Показать категории и приоритеты' }))
    await screen.findByText('Операционная просьба: 1')
    await screen.findByText('Приоритет «Недостаточно данных»: 1')
    view.unmount()
    render(<WeeklyReport />)
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать недельный отчёт' }))
    await screen.findByRole('link', { name: 'Скачать отчёт Markdown' })
    expect(screen.getAllByText(/Неделя:/).length).toBeGreaterThan(0)
  })
  it('повторный запуск дополняет контекст и сохраняет решение', async () => {
    render(<ProcessingPanel />)
    await screen.findByText('Доступен')
    const start = screen.getByRole('button', { name: 'Запустить обработку' })
    await waitFor(() => expect(start).toHaveProperty('disabled', false))
    fireEvent.click(start)
    await screen.findByRole('region', { name: 'Сохранённый запуск' })
    await waitFor(() => expect(screen.getByText('Завершён')).toBeTruthy(), { timeout: 10000 })
    expect(screen.getByLabelText('Покрытие источников').textContent).toContain('полное покрытие: 1')
    const card = await (await nativeFetch(fixture.baseUrl + '/api/v1/topics/' + fixture.topicId)).json()
    expect(card.stats.ticket_count).toBe(1)
    expect(card.topic.status).toBe('needs_research')
    expect(card.history.length).toBe(1)
  })
})
