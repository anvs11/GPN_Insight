import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { workspacePage, emptyArchive } from '../test-fixtures/workspace'
import { api, ApiError } from '../api'
import type { Health, TopicListItem } from '../api-types'
import { topicListItem } from '../test-fixtures/topic'
import { TopicList } from './TopicList'

describe('Список тем из Backend', () => {
  it('различает загрузку и настоящий пустой список', async () => {
    let finish: (items: TopicListItem[]) => void = () => { throw new Error('Запрос не создан') }
    vi.spyOn(api, 'topics').mockReturnValue(new Promise(resolve => { finish = resolve }))
    render(<TopicList />)
    expect(screen.getByText('Загружаем темы…')).toBeTruthy()
    expect(screen.queryByText(/Повторяющихся тем пока нет/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Обновить список' })).toHaveProperty('disabled', true)
    await act(async () => { finish([]) })
    expect(screen.getByText(/Повторяющихся тем пока нет/)).toBeTruthy()
    expect(screen.queryByText('Загружаем темы…')).toBeNull()
  })

  it('показывает сохранённый статус, счётчики и происхождение без заявления о живом анализе', async () => {
    vi.spyOn(api, 'topics').mockResolvedValue([{ ...topicListItem, pending_source_ids: ['SYNTHETIC-UPDATED'] }])
    render(<TopicList />)
    await screen.findByRole('heading', { name: topicListItem.topic.title })
    expect(within(screen.getByRole('article')).getByText('Исследуется')).toBeTruthy()
    expect(screen.getByText('4')).toBeTruthy()
    expect(screen.getByText('Нет данных')).toBeTruthy()
    expect(screen.getByText('Тестовый владелец')).toBeTruthy()
    expect(screen.getByText('Синтетические данные')).toBeTruthy()
    expect(screen.getByText(/подготовлено вручную \(authored\)/)).toBeTruthy()
    expect(screen.getByText(/Результат модельного анализа не подтверждён/)).toBeTruthy()
    expect(screen.getByText(/Источников, ожидающих повторного анализа: 1/)).toBeTruthy()
  })

  it('не заменяет 503 пустым списком и позволяет повторить чтение', async () => {
    const user = userEvent.setup()
    const topics = vi.spyOn(api, 'topics').mockRejectedValueOnce(new ApiError(503, {
      code: 'DATABASE_UNAVAILABLE', message: 'База недоступна. Повторите позже.',
    })).mockResolvedValue([topicListItem])
    render(<TopicList />)
    await screen.findByText('HTTP 503 · DATABASE_UNAVAILABLE')
    expect(screen.queryByText(/Повторяющихся тем пока нет/)).toBeNull()
    expect(screen.queryByRole('list', { name: 'Сохранённые темы' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Обновить список' }))
    await screen.findByRole('heading', { name: topicListItem.topic.title })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(topics).toHaveBeenCalledTimes(2)
  })

  it('при ошибке обновления сохраняет последний успешный список с предупреждением', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topics').mockResolvedValueOnce([topicListItem]).mockRejectedValue(new ApiError(0, {
      code: 'NETWORK_UNAVAILABLE', message: 'Связь потеряна.',
    }))
    render(<TopicList />)
    await screen.findByRole('heading', { name: topicListItem.topic.title })
    await user.click(screen.getByRole('button', { name: 'Обновить список' }))
    await screen.findByText('Связь потеряна.')
    expect(screen.getByRole('heading', { name: topicListItem.topic.title })).toBeTruthy()
    expect(screen.getByText('Показан последний полученный список. Обновление не подтверждено.')).toBeTruthy()
  })

  it('отменяет прежний запрос в StrictMode и не подменяет список запоздалым ответом', async () => {
    let finishOld: (items: TopicListItem[]) => void = () => { throw new Error('Запрос не создан') }
    const old = new Promise<TopicListItem[]>(resolve => { finishOld = resolve })
    const topics = vi.spyOn(api, 'topics').mockReturnValueOnce(old).mockResolvedValue([topicListItem])
    render(<StrictMode><TopicList /></StrictMode>)
    await screen.findByRole('heading', { name: topicListItem.topic.title })
    expect(topics.mock.calls[0][0]?.aborted).toBe(true)
    await act(async () => { finishOld([]) })
    expect(screen.getByRole('heading', { name: topicListItem.topic.title })).toBeTruthy()
    expect(screen.queryByText(/Повторяющихся тем пока нет/)).toBeNull()
  })

  it('читает темы при неготовом анализе и сохраняет период панели при ошибке списка', async () => {
    const user = userEvent.setup()
    const health: Health = { status: 'ok', model_mode: 'replay', processing_state: 'idle',
      pipeline_ready: false, integration_errors: ['ANALYSIS_NOT_CONNECTED'], available_models: {},
      replay_origins: [], replay_record_count: 0 }
    vi.spyOn(api, 'health').mockResolvedValue(health)
    vi.spyOn(api, 'monitoring').mockResolvedValueOnce(workspacePage).mockRejectedValue(new ApiError(503, {
      code: 'DATABASE_UNAVAILABLE', message: 'Список временно недоступен.',
    }))
    vi.spyOn(api, 'reportArchive').mockResolvedValue(emptyArchive)
    const start = vi.spyOn(api, 'startRun')
    render(<App />)
    await screen.findByRole('button', { name: topicListItem.topic.title })
    await user.click(screen.getByRole('button', { name: /Обработка$/ }))
    await screen.findByText('Доступен')
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('С даты'), { target: { value: '2026-10-01' } })
    await user.click(screen.getByRole('button', { name: 'Закрыть: Обработка и состояние' }))
    await screen.findByText(/Список временно недоступен/)
    await user.click(screen.getByRole('button', { name: /Обработка$/ }))
    expect(within(screen.getByRole('dialog')).getByLabelText('С даты')).toHaveProperty('value', '2026-10-01')
    expect(screen.getByRole('button', { name: 'Запустить обработку' })).toHaveProperty('disabled', true)
    expect(screen.queryByText('Экспорт списка заявок')).toBeNull()
    expect(start).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})
