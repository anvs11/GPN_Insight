import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { ImportPreview, ImportResult } from '../api-types'
import { importEntry, importPreview, importResult } from '../test-fixtures/import'
import { textManifest } from '../import-manifest'
import { ImportPanel } from './ImportPanel'

beforeEach(() => { vi.spyOn(api, 'imports').mockResolvedValue([]) })

function fill() {
  fireEvent.change(screen.getByLabelText('ID обращения'), { target: { value: 'synthetic-source' } })
  fireEvent.change(screen.getByLabelText('Дата с часовым поясом'), { target: { value: '2026-10-09T12:00:00+03:00' } })
  fireEvent.change(screen.getByLabelText('Текст обращения'), { target: { value: 'Синтетический запрос на изменение контакта' } })
}
async function preview() {
  fireEvent.submit(screen.getByRole('form', { name: 'Текстовый импорт' }))
  await screen.findByRole('button', { name: 'Подтвердить импорт' })
}
describe('Предпросмотр, подтверждение и сохранность ввода', () => {
  it('сохраняет только после явного подтверждения, без запуска анализа и повторного commit', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    const commit = vi.spyOn(api, 'commitImport').mockResolvedValue(importResult)
    const run = vi.spyOn(api, 'startRun')
    render(<ImportPanel />); fill(); await preview()
    expect(commit).not.toHaveBeenCalled()
    expect(screen.getByText('Набор прошёл проверку. Импорт ещё не выполнен.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт' }))
    await screen.findByRole('region', { name: 'Подтверждённый импорт' })
    expect(screen.getByText(/Это подтверждение импорта, а не успешного ИИ-анализа/)).toBeTruthy()
    expect(commit).toHaveBeenCalledTimes(1); expect(run).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Подтвердить импорт' })).toBeNull()
    expect(screen.getByLabelText('Текст обращения')).toHaveProperty('value', 'Синтетический запрос на изменение контакта')
    expect(screen.getByRole('link', { name: 'Перейти к темам и карточкам' }).getAttribute('href')).toBe('#topic-list-title')
  })
  it('отменяет preview при любой правке и игнорирует его поздний ответ', async () => {
    let resolve: (value: ImportPreview) => void = () => {}
    const check = vi.spyOn(api, 'previewImport').mockReturnValue(new Promise(done => { resolve = done }))
    render(<ImportPanel />); fill()
    fireEvent.submit(screen.getByRole('form', { name: 'Текстовый импорт' }))
    const signal = check.mock.calls[0][1]
    fireEvent.change(screen.getByLabelText('Текст обращения'), { target: { value: 'Изменённый синтетический текст' } })
    expect(signal?.aborted).toBe(true)
    await act(async () => { resolve(importPreview) })
    expect(screen.queryByRole('button', { name: 'Подтвердить импорт' })).toBeNull()
    expect(screen.getByLabelText('Текст обращения')).toHaveProperty('value', 'Изменённый синтетический текст')
  })
  it('прежнее разрешение сбрасывается даже при правке ID заявителя', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    render(<ImportPanel />); fill(); await preview()
    fireEvent.change(screen.getByLabelText('ID заявителя (необязательно)'), { target: { value: 'synthetic-requester' } })
    expect(screen.queryByRole('button', { name: 'Подтвердить импорт' })).toBeNull()
  })
  it('не дублирует commit при быстрых повторных нажатиях и блокирует изменение формы', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    let finish: (value: ImportResult) => void = () => {}
    const commit = vi.spyOn(api, 'commitImport').mockReturnValue(new Promise(resolve => { finish = resolve }))
    render(<ImportPanel />); fill(); await preview()
    const button = screen.getByRole('button', { name: 'Подтвердить импорт' })
    fireEvent.click(button); fireEvent.click(button)
    expect(commit).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('group', { name: '1. Подготовьте данные' })).toHaveProperty('disabled', true)
    await act(async () => { finish(importResult) })
    await screen.findByRole('region', { name: 'Подтверждённый импорт' })
  })
  it('не включает подтверждение при invalid preview, даже если HTTP успешен', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue({ ...importPreview, status: 'invalid', can_commit: false,
      counts: { inserted: 0, updated: 0, unchanged: 0, invalid: 1 },
      errors: [{ code: 'SOURCE_ID_DUPLICATE', message: 'ID повторяется.', source_id: 'synthetic-source', asset_id: null, field: 'tickets.1.source_id' }] })
    render(<ImportPanel />); fill(); await preview()
    expect(screen.getByText('ID повторяется.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подтвердить импорт' })).toHaveProperty('disabled', true)
  })
  it.each([409, 413, 422])('после HTTP %s сохраняет ввод, но требует нового предпросмотра', async status => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    vi.spyOn(api, 'commitImport').mockRejectedValue(new ApiError(status, { code: 'IMPORT_ERROR', message: 'Запись отклонена.' }))
    render(<ImportPanel />); fill(); await preview()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт' }))
    await screen.findByText('Запись отклонена.')
    expect(screen.getByLabelText('Текст обращения')).toHaveProperty('value', 'Синтетический запрос на изменение контакта')
    expect(screen.queryByRole('button', { name: 'Подтвердить импорт' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Подтверждённый импорт' })).toBeNull()
    expect(screen.queryByText(/Результат импорта не подтверждён/)).toBeNull()
  })
  it.each([0, 503, 200])('при неопределённом HTTP %s допускает только чтение, не принимает журнал за квитанцию', async status => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    const commit = vi.spyOn(api, 'commitImport').mockRejectedValue(new ApiError(status, { code: 'UNCERTAIN', message: 'Ответ потерян.' }))
    const history = vi.spyOn(api, 'imports').mockResolvedValue([importEntry])
    render(<ImportPanel />); fill(); await preview()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт' }))
    await screen.findByText(/Результат импорта не подтверждён/)
    fireEvent.submit(screen.getByRole('form', { name: 'Текстовый импорт' }))
    fireEvent.click(screen.getByRole('button', { name: 'Проверить журнал без повторной записи' }))
    await screen.findByRole('region', { name: 'Журнал импортов' })
    expect(history).toHaveBeenCalledTimes(1); expect(commit).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('region', { name: 'Подтверждённый импорт' })).toBeNull()
    expect(screen.getByText(/Совпадение dataset_id само по себе не доказывает/)).toBeTruthy()
  })
  it('не принимает квитанцию другого набора как успех', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    vi.spyOn(api, 'commitImport').mockResolvedValue({ ...importResult, dataset_id: 'other' })
    render(<ImportPanel />); fill(); await preview()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт' }))
    await screen.findByText(/Результат импорта не подтверждён/)
    expect(screen.queryByRole('region', { name: 'Подтверждённый импорт' })).toBeNull()
  })
  it('сеть при preview не уничтожает поля и не разрешает импорт', async () => {
    vi.spyOn(api, 'previewImport').mockRejectedValue(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Backend недоступен.' }))
    render(<ImportPanel />); fill()
    fireEvent.submit(screen.getByRole('form', { name: 'Текстовый импорт' }))
    await screen.findByText('Backend недоступен.')
    expect(screen.getByLabelText('ID обращения')).toHaveProperty('value', 'synthetic-source')
    expect(screen.queryByRole('button', { name: 'Подтвердить импорт' })).toBeNull()
  })
  it('действительный повтор показывает unchanged, а не ещё один новый тикет', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    vi.spyOn(api, 'commitImport').mockResolvedValue({ ...importResult, counts: { inserted: 0, updated: 0, unchanged: 1, invalid: 0 } })
    render(<ImportPanel />); fill(); await preview()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт' }))
    const result = await screen.findByRole('region', { name: 'Подтверждённый импорт' })
    expect(within(result).getByText('Без изменений').nextElementSibling?.textContent).toBe('1')
    expect(within(result).getByText('Новых').nextElementSibling?.textContent).toBe('0')
  })
  it('не отправляет пустое обращение', () => {
    const check = vi.spyOn(api, 'previewImport')
    render(<ImportPanel />); fill()
    fireEvent.change(screen.getByLabelText('Текст обращения'), { target: { value: '   ' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Текстовый импорт' }))
    expect(screen.getByRole('alert').textContent).toContain('непустой text')
    expect(check).not.toHaveBeenCalled()
  })
  it('поддерживает пустой JSON-набор без создания пустой регистрации', async () => {
    vi.spyOn(api, 'previewImport').mockResolvedValue({ ...importPreview, counts: { inserted: 0, updated: 0, unchanged: 0, invalid: 0 } })
    render(<ImportPanel />)
    fireEvent.change(screen.getByLabelText('Способ ввода'), { target: { value: 'file' } })
    fireEvent.change(screen.getByLabelText(/Текстовый JSON-файл/), { target: { files: [{
      name: 'empty.json', size: 100, arrayBuffer: async () => new TextEncoder().encode(JSON.stringify({ dataset_id: importPreview.dataset_id, synthetic: true, tickets: [] })).buffer,
    }] } })
    await screen.findByText(/Прочитан файл: empty.json/)
    await preview()
    expect(screen.getByText('В наборе нет обращений. Импортировать нечего.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подтвердить импорт' })).toHaveProperty('disabled', true)
  })
  it('не использует результат чтения старого файла после выбора нового', async () => {
    let finish: (raw: ArrayBuffer) => void = () => {}
    render(<ImportPanel />)
    fireEvent.change(screen.getByLabelText('Способ ввода'), { target: { value: 'file' } })
    const input = screen.getByLabelText(/Текстовый JSON-файл/)
    fireEvent.change(input, { target: { files: [{ name: 'old.json', size: 200, arrayBuffer: () => new Promise(resolve => { finish = resolve }) }] } })
    fireEvent.change(input, { target: { files: [{ name: 'new.json', size: 200, arrayBuffer: async () => new TextEncoder().encode(textManifest('new', {
      source_id: 'new', text: 'Синтетика', created_at: '2026-10-01T12:00:00+03:00', requester_id: '',
    })).buffer }] } })
    await screen.findByText(/Прочитан файл: new.json/)
    await act(async () => { finish(new TextEncoder().encode('{}').buffer) })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText(/Прочитан файл: new.json/)).toBeTruthy()
  })
  it('сохраняет старый журнал с предупреждением, игнорирует отменённый ответ', async () => {
    const history = vi.spyOn(api, 'imports').mockResolvedValueOnce([importEntry])
      .mockRejectedValueOnce(new ApiError(503, { code: 'DATABASE_UNAVAILABLE', message: 'Журнал недоступен.' }))
    render(<ImportPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить журнал импортов' }))
    await screen.findByRole('region', { name: 'Журнал импортов' })
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить журнал импортов' }))
    await screen.findByText('Журнал недоступен.')
    expect(screen.getByText(/Показан ранее полученный журнал/)).toBeTruthy()
    expect(history).toHaveBeenCalledTimes(2)
  })
  it('кнопки предпросмотра доступны с клавиатуры', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    render(<ImportPanel />); fill()
    screen.getByRole('button', { name: 'Предпросмотр' }).focus()
    await user.keyboard('{Enter}')
    await screen.findByRole('button', { name: 'Подтвердить импорт' })
    expect(screen.getByLabelText('ID обращения')).toHaveProperty('value', 'synthetic-source')
  })

  it('журнал от старого запроса не заменяет более новый ответ', async () => {
    let finish: (value: typeof importEntry[]) => void = () => {}
    const history = vi.spyOn(api, 'imports').mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
      .mockResolvedValueOnce([])
    render(<ImportPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить журнал импортов' }))
    const signal = history.mock.calls[0][0]
    fireEvent.click(screen.getByRole('button', { name: 'Обновить запрос журнала' }))
    await screen.findByText('Завершённых импортов нет.')
    expect(signal?.aborted).toBe(true)
    await act(async () => { finish([importEntry]) })
    expect(screen.queryByText(/ID регистрации: synthetic-registration/)).toBeNull()
  })
  it('не заменяет ошибочный UTF-8 символом замены перед отправкой', async () => {
    const check = vi.spyOn(api, 'previewImport')
    render(<ImportPanel />)
    fireEvent.change(screen.getByLabelText('Способ ввода'), { target: { value: 'file' } })
    fireEvent.change(screen.getByLabelText(/Текстовый JSON-файл/), { target: { files: [{
      name: 'invalid.json', size: 1, arrayBuffer: async () => new Uint8Array([255]).buffer,
    }] } })
    await screen.findByText(/Файл должен быть корректным UTF-8 JSON без BOM/)
    expect(check).not.toHaveBeenCalled()
  })
  it('извещает панель обработки только после принятой квитанции, а не предпросмотра', async () => {
    const onImported = vi.fn()
    vi.spyOn(api, 'previewImport').mockResolvedValue(importPreview)
    vi.spyOn(api, 'commitImport').mockResolvedValue(importResult)
    render(<ImportPanel onImported={onImported} />); fill(); await preview()
    expect(onImported).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить импорт' }))
    await screen.findByRole('region', { name: 'Подтверждённый импорт' })
    expect(onImported).toHaveBeenCalledTimes(1)
  })
})
