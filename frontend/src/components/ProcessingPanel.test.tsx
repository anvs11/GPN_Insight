import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { Health, Run } from '../api-types'
import { ProcessingPanel } from './ProcessingPanel'

// Только синтетические ответы для проверок UI; production использует настоящий api.ts.
const health: Health = {
  status: 'ok', model_mode: 'replay', processing_state: 'idle', pipeline_ready: false,
  integration_errors: ['NORMALIZER_NOT_CONNECTED', 'ANALYSIS_NOT_CONNECTED'],
  available_models: { text: { status: 'not_configured', configured: false, checked_at: null,
    code: 'REPLAY_NOT_CONFIGURED', message: 'Набор ответов не настроен.' } },
  replay_origins: [], replay_record_count: 0,
}
const run: Run = {
  run_id: 'synthetic-ui-run', product_id: 'webneft', mode: 'replay', state: 'completed', stage: 'finished',
  successful_materials: 1, failed_materials: 0, total_materials: 1, coverage: 1, errors: [],
  created_at: '2026-11-05T10:00:00+03:00', started_at: null, finished_at: null,
  origins: ['authored'], completed_steps: 3, reused_steps: 0, failure_code: null, failure_message: null,
}

describe('Состояние обработки', () => {
  it('различает исправный сервер и незавершённую интеграцию, блокируя запуск', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const start = vi.spyOn(api, 'startRun')
    render(<ProcessingPanel />)
    await screen.findByText('Доступен')
    expect(screen.getByRole('button', { name: 'Запустить обработку' })).toHaveProperty('disabled', true)
    expect(screen.getByText(/Подготовка материалов участника №1/)).toBeTruthy()
    expect(screen.getByText(/Анализ участника №2/)).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Сохранённый запуск' })).toBeNull()
    expect(start).not.toHaveBeenCalled()
  })

  it('обрабатывает 503 при устаревшем признаке готовности, сохраняя период', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'health').mockResolvedValueOnce({ ...health, pipeline_ready: true }).mockResolvedValue(health)
    vi.spyOn(api, 'startRun').mockRejectedValue(new ApiError(503, {
      code: 'INTEGRATION_NOT_READY', message: 'Компоненты не подключены. Запуск не создан.',
    }))
    render(<ProcessingPanel />)
    await screen.findByText('Доступен')
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-10-31' } })
    await user.click(screen.getByRole('button', { name: 'Запустить обработку' }))
    await screen.findByText('HTTP 503 · INTEGRATION_NOT_READY')
    expect(screen.getByLabelText('С даты')).toHaveProperty('value', '2026-10-01')
    expect(screen.getByLabelText('По дату')).toHaveProperty('value', '2026-10-31')
    expect(sessionStorage.getItem('gpn-insight.processing-draft.v1')).toContain('2026-10-31')
    expect(screen.queryByRole('region', { name: 'Сохранённый запуск' })).toBeNull()
    expect(sessionStorage.getItem('gpn-insight.last-run.v1')).toBeNull()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Запустить обработку' })).toHaveProperty('disabled', true))
  })

  it('при сетевой ошибке не показывает старую готовность и сохраняет черновик', async () => {
    const user = userEvent.setup()
    sessionStorage.setItem('gpn-insight.processing-draft.v1', '{"from":"2026-10-01","to":"2026-10-31"}')
    vi.spyOn(api, 'health').mockResolvedValueOnce({ ...health, pipeline_ready: true }).mockRejectedValue(
      new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Сервер недоступен.' }))
    render(<ProcessingPanel />)
    await screen.findByText('Доступен')
    await user.click(screen.getByRole('button', { name: 'Обновить состояние' }))
    await screen.findByText('Сервер недоступен.')
    expect(screen.queryByText('Доступен')).toBeNull()
    expect(screen.getByRole('button', { name: 'Запустить обработку' })).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('С даты')).toHaveProperty('value', '2026-10-01')
  })

  it('показывает ошибки анализа при 100% покрытия и вручную подготовленную часть', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'health').mockResolvedValue(health)
    vi.spyOn(api, 'run').mockResolvedValue({ ...run, state: 'completed_with_errors', origins: ['model', 'authored'],
      errors: [{ source_id: 'SYNTHETIC-1', asset_id: null, stage: 'assignment', code: 'CATALOG_CHANGED', message: 'Каталог изменился.' }] })
    render(<ProcessingPanel />)
    await user.type(screen.getByLabelText('ID запуска'), run.run_id)
    await user.click(screen.getByRole('button', { name: 'Показать запуск' }))
    await screen.findByRole('heading', { name: 'Завершён с ошибками' })
    expect(screen.getByText('100%')).toBeTruthy()
    expect(screen.getByText('CATALOG_CHANGED')).toBeTruthy()
    expect(screen.getByText(/model — ответ модели; authored — подготовлено вручную/)).toBeTruthy()
  })

  it('не очищает введённый ID после 404 и не показывает выдуманный результат', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'health').mockResolvedValue(health)
    vi.spyOn(api, 'run').mockRejectedValue(new ApiError(404, { code: 'RUN_NOT_FOUND', message: 'Запуск не найден.' }))
    render(<ProcessingPanel />)
    await user.type(screen.getByLabelText('ID запуска'), 'missing-run')
    await user.click(screen.getByRole('button', { name: 'Показать запуск' }))
    await screen.findByText('Запуск не найден.')
    expect(screen.getByLabelText('ID запуска')).toHaveProperty('value', 'missing-run')
    expect(screen.queryByRole('region', { name: 'Сохранённый запуск' })).toBeNull()
  })

  it('восстанавливает последний ID, опрашивает активный запуск и останавливается после завершения', async () => {
    vi.useFakeTimers()
    sessionStorage.setItem('gpn-insight.last-run.v1', run.run_id)
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const getRun = vi.spyOn(api, 'run').mockResolvedValueOnce({ ...run, state: 'running', stage: 'assignment' }).mockResolvedValue(run)
    render(<ProcessingPanel />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('heading', { name: 'Обрабатывается' })).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(screen.getByRole('heading', { name: 'Завершён' })).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(getRun).toHaveBeenCalledTimes(2)
  })

  it('отклоняет перевёрнутый период до отправки запроса', async () => {
    vi.spyOn(api, 'health').mockResolvedValue({ ...health, pipeline_ready: true })
    const start = vi.spyOn(api, 'startRun')
    render(<ProcessingPanel />)
    await screen.findByText('Доступен')
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-10-31' } })
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-10-01' } })
    expect(screen.getByRole('button', { name: 'Запустить обработку' })).toHaveProperty('disabled', true)
    expect(start).not.toHaveBeenCalled()
  })

  it('после 503 сохраняет режим live, выбранный сервером', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'health').mockResolvedValue({ ...health, model_mode: 'live', pipeline_ready: true })
    vi.spyOn(api, 'startRun').mockRejectedValue(new ApiError(503, {
      code: 'INTEGRATION_NOT_READY', message: 'Запуск не создан.',
    }))
    render(<ProcessingPanel />)
    await screen.findByText('live')
    await user.click(screen.getByRole('button', { name: 'Запустить обработку' }))
    await screen.findByText('HTTP 503 · INTEGRATION_NOT_READY')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Обновить состояние' })).toHaveProperty('disabled', false))
    expect(screen.getByText('live')).toBeTruthy()
    expect(screen.queryByText('replay')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Сохранённый запуск' })).toBeNull()
  })

  it('в StrictMode отменяет прежнюю проверку и игнорирует её запоздалый ответ', async () => {
    let finishOldRequest: (value: Health) => void = () => { throw new Error('Запрос не создан') }
    const oldRequest = new Promise<Health>(resolve => { finishOldRequest = resolve })
    const check = vi.spyOn(api, 'health').mockReturnValueOnce(oldRequest)
      .mockResolvedValue({ ...health, model_mode: 'live' })
    render(<StrictMode><ProcessingPanel /></StrictMode>)
    await screen.findByText('live')
    expect(check.mock.calls[0][0]?.aborted).toBe(true)
    await act(async () => { finishOldRequest(health) })
    expect(screen.getByText('live')).toBeTruthy()
    expect(screen.queryByText('replay')).toBeNull()
  })

  it('при закрытии панели прекращает опрос и сохраняет данные этой вкладки', async () => {
    vi.useFakeTimers()
    sessionStorage.setItem('gpn-insight.last-run.v1', run.run_id)
    sessionStorage.setItem('gpn-insight.processing-draft.v1', '{"from":"2026-10-01","to":"2026-10-31"}')
    sessionStorage.setItem('user-setting', 'unchanged')
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const getRun = vi.spyOn(api, 'run').mockResolvedValue({ ...run, state: 'running', stage: 'assignment' })
    const view = render(<ProcessingPanel />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    view.unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(getRun).toHaveBeenCalledTimes(1)
    expect(getRun.mock.calls[0][1]?.aborted).toBe(true)
    expect(sessionStorage.getItem('gpn-insight.last-run.v1')).toBe(run.run_id)
    expect(sessionStorage.getItem('gpn-insight.processing-draft.v1')).toContain('2026-10-31')
    expect(sessionStorage.getItem('user-setting')).toBe('unchanged')
  })
})
