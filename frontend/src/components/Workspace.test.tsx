import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api, ApiError } from '../api'
import { workspaceCard, workspacePage, emptyArchive } from '../test-fixtures/workspace'
import { topicDetail } from '../test-fixtures/detail'
import type { ReportSnapshot, WorkspacePage } from '../workspace-contracts'

const snapshot: ReportSnapshot = { format_version: 'report-snapshot-v1', report_id: 'synthetic-report', title: 'Обзор для руководителя', kind: 'selection', generated_at: '2026-11-10T12:00:00Z',
  model_mode: 'replay', period: null, selection_count: 1, digest: 'a'.repeat(64), markdown: '# Synthetic\n<script>untrusted</script>', summary: '', comment: '',
  limitations: 'Синтетический authored снимок', cards: [workspaceCard], selections: [{ kind: 'topic', id: workspaceCard.id, version: workspaceCard.version, revision: workspaceCard.revision }] }
function setup() {
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', model_mode: 'replay', processing_state: 'idle', pipeline_ready: true,
    integration_errors: [], available_models: {}, replay_origins: ['authored'], replay_record_count: 1 })
  vi.spyOn(api, 'imports').mockResolvedValue([])
  vi.spyOn(api, 'monitoring').mockResolvedValue(workspacePage)
  vi.spyOn(api, 'reportArchive').mockResolvedValue(emptyArchive)
  vi.spyOn(api, 'weeklySchedule').mockResolvedValue({ enabled: true, next_run_at: '2026-11-15T21:00:00Z', last_error: null })
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:synthetic-report'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
}
function reports() { fireEvent.click(within(screen.getByRole('navigation', { name: 'Основные разделы' })).getByRole('button', { name: /Отчёты/ })) }
function monitoring() { fireEvent.click(within(screen.getByRole('navigation', { name: 'Основные разделы' })).getByRole('button', { name: /Мониторинг/ })) }

describe('Рабочее пространство мониторинга и отчётов', () => {
  it('сохраняет тему после повторного открытия приложения и позволяет вернуться к светлой', () => {
    setup()
    const previous = localStorage.getItem('gpn-workspace-theme')
    try {
      localStorage.setItem('gpn-workspace-theme', 'dark')
      const first = render(<App />)
      expect(document.documentElement.dataset.theme).toBe('dark')
      fireEvent.click(screen.getByRole('button', { name: 'Светлая тема' }))
      expect(document.documentElement.dataset.theme).toBe('light')
      expect(localStorage.getItem('gpn-workspace-theme')).toBe('light')
      first.unmount()
      const reopened = render(<App />)
      expect(document.documentElement.dataset.theme).toBe('light')
      fireEvent.click(screen.getByRole('button', { name: 'Тёмная тема' }))
      expect(document.documentElement.dataset.theme).toBe('dark')
      expect(localStorage.getItem('gpn-workspace-theme')).toBe('dark')
      reopened.unmount()
    } finally {
      if (previous === null) localStorage.removeItem('gpn-workspace-theme'); else localStorage.setItem('gpn-workspace-theme', previous)
      document.documentElement.dataset.theme = 'light'
    }
  })
  it('клик по карточке открывает центрированный диалог, быстрый выбор не открывает его', async () => {
    setup(); vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить в отчёт: ' + workspaceCard.title }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('checkbox', { name: 'В отчёт: ' + workspaceCard.title })).toHaveProperty('checked', true)
    fireEvent.click(within(screen.getByRole('article')).getByText(workspaceCard.summary))
    await screen.findByRole('dialog', { name: 'Тема и решение' })
    fireEvent.click(document.querySelector('.drawer-backdrop:not([hidden])')!)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('открывает архив в отдельном окне, PDF скачивается отдельно от печати', async () => {
    setup(); vi.spyOn(api, 'reportArchive').mockResolvedValue({ ...emptyArchive, items: [snapshot], total: 1 })
    vi.spyOn(api, 'archivedReport').mockResolvedValue(snapshot)
    const pdf = vi.spyOn(api, 'reportPdf').mockResolvedValue(new Blob(['%PDF-synthetic'], { type: 'application/pdf' }))
    const print = vi.spyOn(window, 'print').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    render(<App />); reports()
    expect(document.querySelector('#print-report')).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: snapshot.title }))
    const viewer = await screen.findByRole('dialog', { name: 'Просмотр отчёта' })
    await within(viewer).findByText(/<script>untrusted/)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить PDF' }))
    await waitFor(() => expect(pdf).toHaveBeenCalledWith(snapshot.report_id, expect.any(AbortSignal)))
    expect(print).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Печать' }))
    await waitFor(() => expect(print).toHaveBeenCalledTimes(1))
  })

  it('загрузка содержит только текст и медиа, обработка вынесена отдельно', async () => {
    setup(); render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /Загрузить данные/ }))
    const dialog = screen.getByRole('dialog', { name: 'Загрузка обращений' })
    expect(within(dialog).getByRole('button', { name: 'Текст' })).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: 'Медиа' })).toBeTruthy()
    expect(within(dialog).queryByRole('button', { name: 'Обработка' })).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Медиа' }))
    expect(within(dialog).getByRole('list', { name: 'Шаги медиаимпорта' })).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: /Правила работы/ }))
    expect(within(dialog).getByRole('button', { name: /Правила работы/ }).getAttribute('aria-expanded')).toBe('true')
  })

  it('сохраняет выбор и черновик при переходах, отправляет точную версию и не интерпретирует HTML отчёта', async () => {
    setup(); const save = vi.spyOn(api, 'saveReport').mockResolvedValue(snapshot)
    render(<App />)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'В отчёт: ' + workspaceCard.title }))
    reports()
    fireEvent.change(screen.getByLabelText('Краткое резюме'), { target: { value: 'Мой контекст' } })
    monitoring()
    expect(screen.getByRole('checkbox', { name: 'В отчёт: ' + workspaceCard.title })).toHaveProperty('checked', true)
    reports()
    expect(screen.getByLabelText('Краткое резюме')).toHaveProperty('value', 'Мой контекст')
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить подборку в архив' }))
    await screen.findByText(/Отчёт сохранён на сервере/)
    expect(save).toHaveBeenCalledWith({ title: 'Обзор для руководителя', summary: 'Мой контекст', comment: '', selections: [{ kind: 'topic', id: workspaceCard.id, version: workspaceCard.version, revision: workspaceCard.revision }] })
    expect(await screen.findByRole('link', { name: 'Скачать Markdown' })).toBeTruthy()
    expect(screen.queryByRole('dialog', { name: 'Просмотр отчёта' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Просмотр' }))
    expect(document.querySelector('#print-report script')).toBeNull()
    expect(screen.getByText(/<script>untrusted<\/script>/)).toBeTruthy()
  })

  it('блокирует повтор сохранения и навигацию, 409 сохраняет подборку и комментарии', async () => {
    setup()
    let reject: (value: unknown) => void = () => {}
    const save = vi.spyOn(api, 'saveReport').mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
    render(<App />)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'В отчёт: ' + workspaceCard.title })); reports()
    fireEvent.change(screen.getByLabelText('Комментарий автора'), { target: { value: 'Не потерять' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить подборку в архив' }))
    expect(within(screen.getByRole('navigation')).getByRole('button', { name: /Мониторинг/ })).toHaveProperty('disabled', true)
    expect(save).toHaveBeenCalledTimes(1)
    await act(async () => reject(new ApiError(409, { code: 'REPORT_SELECTION_STALE', message: 'Объект изменился.' })))
    await screen.findByText(/Объект изменился/)
    expect(screen.getByLabelText('Комментарий автора')).toHaveProperty('value', 'Не потерять')
    expect(screen.getByRole('button', { name: 'Сохранить подборку в архив' })).toHaveProperty('disabled', false)
    expect(screen.queryByRole('link', { name: 'Скачать Markdown' })).toBeNull()
  })

  it('не смешивает ответ предыдущего представления с обращениями', async () => {
    setup()
    let resolve: (page: WorkspacePage) => void = () => {}
    const load = vi.spyOn(api, 'monitoring').mockImplementationOnce(() => new Promise(done => { resolve = done }))
      .mockResolvedValue({ ...workspacePage, items: [{ ...workspaceCard, kind: 'source', id: 'source-only', title: 'Необработанная заявка', status: 'pending' }] })
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: /Обращения/ }))
    await screen.findByRole('button', { name: 'Необработанная заявка' })
    await act(async () => resolve(workspacePage))
    expect(screen.queryByRole('button', { name: workspaceCard.title })).toBeNull()
    expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ view: 'sources' }), expect.any(AbortSignal))
  })

  it('закрывает панель по Escape и возвращает фокус к карточке', async () => {
    setup(); vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    render(<App />)
    const opener = await screen.findByRole('button', { name: workspaceCard.title })
    opener.focus(); fireEvent.click(opener)
    await screen.findByRole('dialog', { name: 'Тема и решение' })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(opener))
  })

  it('выдаёт потенциальную критичность как сигнал, а неизвестное — отдельно', async () => {
    setup(); vi.spyOn(api, 'monitoring').mockResolvedValue({ ...workspacePage, items: [{ ...workspaceCard, priorities: ['potentially_critical', 'unknown'] }] })
    render(<App />)
    await screen.findByText('Потенциально критичный')
    expect(within(screen.getByRole('article')).getByText('Недостаточно данных')).toBeTruthy()
    expect(screen.getByText(/Replay: сохранённые ответы/)).toBeTruthy()
  })

  it('не закрывает панель по Escape во время сохранения решения', async () => {
    setup(); vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    let fail: (error: unknown) => void = () => {}
    vi.spyOn(api, 'saveDecision').mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: workspaceCard.title }))
    await screen.findByRole('region', { name: 'Решение владельца продукта' })
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: 'Проверяем защиту закрытия' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeTruthy()
    await act(async () => fail(new ApiError(422, { code: 'VALIDATION_ERROR', message: 'Синтетическая ошибка формы' })))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
