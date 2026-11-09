import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import { importPreview, importResult } from '../test-fixtures/import'
import { completedWeek, processingDetail, processingSource, weeklyText } from '../test-fixtures/processing'
import { MediaImportPanel } from './MediaImportPanel'
import { MediaAsset } from './MediaAsset'
import { SourceProcessing } from './SourceProcessing'
import { WeeklyReport } from './WeeklyReport'
import type { MaterialInput } from '../api-types'

const asset: MaterialInput = { asset_id: 'synthetic-image', path: 'ignored-original.png', source_url: null, kind: 'image', mime_type: 'image/png' }
const manifest = JSON.stringify({ dataset_id: importPreview.dataset_id, synthetic: true, tickets: [{
  source_id: 'synthetic-media', created_at: '2026-11-02T12:00:00+03:00', text: '', assets: [{ ...asset, path: 'screen.png' }],
}] })
function jsonFile(raw = manifest) {
  const file = new File([raw], 'manifest.json', { type: 'application/json' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode(raw).buffer })
  return file
}
async function choose() {
  fireEvent.change(screen.getByLabelText('JSON manifest для медиа'), { target: { files: [jsonFile()] } })
  fireEvent.change(screen.getByLabelText('Файлы WAV, MP3, PNG, JPEG'), { target: { files: [new File(['synthetic'], 'screen.png')] } })
  await screen.findByText(/JSON: manifest.json/)
  fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр медиа' }))
  await screen.findByRole('button', { name: 'Подтвердить импорт медиа' })
}
function urls() {
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:synthetic-test'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
}
afterEach(() => vi.restoreAllMocks())

describe('Медиаимпорт и недельная сводка', () => {
  it('сохраняет точный File snapshot, не удваивает commit и подтверждает только receipt', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    let finish: (v: typeof importResult) => void = () => {}
    const commit = vi.spyOn(api, 'commitImport').mockReturnValue(new Promise(resolve => { finish = resolve }))
    const imported = vi.fn()
    render(<MediaImportPanel onImported={imported} />)
    await choose()
    const button = screen.getByRole('button', { name: 'Подтвердить импорт медиа' })
    fireEvent.click(button); fireEvent.click(button)
    expect(commit).toHaveBeenCalledTimes(1)
    expect(imported).not.toHaveBeenCalled()
    expect(commit.mock.calls[0][0]).toBe(manifest)
    expect(commit.mock.calls[0][2]?.[0].name).toBe('screen.png')
    expect(screen.getByRole('group', { name: 'Набор с вложениями' })).toHaveProperty('disabled', true)
    await act(async () => finish(importResult))
    await screen.findByRole('region', { name: 'Подтверждённый медиаимпорт' })
    expect(imported).toHaveBeenCalledTimes(1)
  })
  it('смена файла отменяет старый preview и не разрешает commit', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    render(<MediaImportPanel />); await choose()
    fireEvent.change(screen.getByLabelText('Файлы WAV, MP3, PNG, JPEG'), { target: { files: [] } })
    expect(screen.queryByRole('button', { name: 'Подтвердить импорт медиа' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр медиа' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Выберите все файлы'))
  })
  it('не связывает прежнюю квитанцию с источниками нового неуспешного commit', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    vi.spyOn(api, 'commitImport').mockResolvedValueOnce(importResult)
      .mockRejectedValueOnce(new ApiError(422, { code: 'VALIDATION_ERROR', message: 'Новый набор отклонён.' }))
    render(<MediaImportPanel />); await choose()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт медиа' }))
    await screen.findByRole('region', { name: 'Подтверждённый медиаимпорт' })
    fireEvent.change(screen.getByLabelText('JSON manifest для медиа'), { target: { files: [jsonFile(manifest.replace('synthetic-media', 'synthetic-new'))] } })
    await screen.findByText(/JSON: manifest.json/)
    fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр медиа' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Подтвердить импорт медиа' }))
    await screen.findByText('Новый набор отклонён.')
    const receipt = within(screen.getByRole('region', { name: 'Подтверждённый медиаимпорт' }))
    expect(receipt.getByRole('button', { name: 'Сверить медиаисточник synthetic-media' })).toBeTruthy()
    expect(receipt.queryByRole('button', { name: 'Сверить медиаисточник synthetic-new' })).toBeNull()
  })
  it('после неизвестного commit не повторяет запись и предлагает сверку', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    const commit = vi.spyOn(api, 'commitImport').mockRejectedValue(new ApiError(503, { code: 'IMPORT_CLEANUP_PENDING', message: 'Проверьте результат.' }))
    render(<MediaImportPanel />); await choose()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт медиа' }))
    await screen.findByText(/Результат записи неизвестен/)
    expect(screen.getByRole('group', { name: 'Набор с вложениями' })).toHaveProperty('disabled', true)
    expect(commit).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Сверить медиаисточник synthetic-media' })).toBeTruthy()
  })
  it('показывает partial/unknown/authored и не выдаёт отсутствие оценки за normal', async () => {
    vi.spyOn(api, 'processing').mockResolvedValue(processingDetail)
    render(<SourceProcessing source={processingSource} />)
    await screen.findByText(/частичное покрытие/)
    expect(screen.getByText(/Приоритет: Недостаточно данных/)).toBeTruthy()
    expect(screen.getByText(/подготовлено вручную \(authored\)/)).toBeTruthy()
  })
  it('не загружает медиа автоматически, освобождает Blob и не использует original path', async () => {
    urls()
    const source = { ...processingSource, ticket: { ...processingSource.ticket, assets: [asset] } }
    const media = vi.spyOn(api, 'media').mockResolvedValue(new Blob(['synthetic'], { type: 'image/png' }))
    const view = render(<MediaAsset source={source} asset={asset} />)
    expect(media).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть медиа synthetic-image' }))
    const image = await screen.findByRole('img')
    expect(image.getAttribute('src')).toBe('blob:synthetic-test')
    expect(image.getAttribute('src')).not.toContain(asset.path)
    view.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-test')
  })
  it('не предлагает открывать несинтетический оригинал', () => {
    const media = vi.spyOn(api, 'media')
    render(<MediaAsset source={{ ...processingSource, synthetic: false }} asset={asset} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(media).not.toHaveBeenCalled()
  })
  it('формирует серверную неделю, экранирует Markdown и блокирует stale скачивание', async () => {
    urls()
    vi.spyOn(api, 'weeklyPeriod').mockResolvedValue(completedWeek)
    const report = vi.spyOn(api, 'weeklyReport').mockResolvedValue(weeklyText)
    const view = render(<WeeklyReport />)
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать недельный отчёт' }))
    const link = await screen.findByRole('link', { name: 'Скачать отчёт Markdown' })
    expect(link.getAttribute('download')).toBe('gpn-insight-2026-11-02.md')
    expect(view.container.querySelector('script')).toBeNull()
    expect(view.container.querySelector('pre')?.textContent).toContain('<script>doNotRun()</script>')
    report.mockRejectedValue(new ApiError(503, { code: 'DATABASE_UNAVAILABLE', message: 'База недоступна.' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать недельный отчёт' }))
    await screen.findByText('База недоступна.')
    expect(screen.queryByRole('link', { name: 'Скачать отчёт Markdown' })).toBeNull()
    expect(screen.getByText(/Ниже последний полученный отчёт/)).toBeTruthy()
    view.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })
  it('не скачивает отчёт с периодом, отличным от подтверждённого', async () => {
    vi.spyOn(api, 'weeklyPeriod').mockResolvedValue(completedWeek)
    vi.spyOn(api, 'weeklyReport').mockResolvedValue('Неделя: другой период')
    render(<WeeklyReport />)
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать недельный отчёт' }))
    await screen.findByText(/Период отчёта изменился/)
    expect(screen.queryByRole('link')).toBeNull()
  })
})
