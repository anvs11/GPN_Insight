import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { TopicListItem } from '../api-types'
import { topicListItem } from '../test-fixtures/topic'
import { savedDecision, topicDetail } from '../test-fixtures/detail'
import { TopicList } from './TopicList'

const period = { ...topicDetail.stats, ticket_count: 1, requester_count: null, date_from: '2026-11-01', date_to: '2026-11-01' }
function dates() {
  fireEvent.change(screen.getByLabelText('Темы с даты'), { target: { value: period.date_from } })
  fireEvent.change(screen.getByLabelText('Темы по дату'), { target: { value: period.date_to } })
}
function submit() { fireEvent.submit(screen.getByRole('form', { name: 'Фильтры тем' })) }

describe('Фильтры списка тем', () => {
  it('применяет комбинацию только по кнопке и разделяет общую и периодную статистику', async () => {
    const user = userEvent.setup()
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([topicListItem])
      .mockResolvedValue([{ ...topicListItem, period_stats: period }])
    render(<TopicList />)
    await screen.findByRole('article')
    await user.selectOptions(screen.getByLabelText('Статус темы'), 'needs_research')
    await user.selectOptions(screen.getByLabelText('Повторяемость'), 'all')
    dates()
    expect(topics).toHaveBeenCalledTimes(1)
    submit()
    await screen.findByRole('group', { name: 'Статистика выбранного периода' })
    expect(topics).toHaveBeenLastCalledWith(expect.any(AbortSignal), {
      status: 'needs_research', support_level: 'all', date_from: period.date_from, date_to: period.date_to,
    })
    expect(within(screen.getByRole('group', { name: 'Статистика за всё время' })).getByText('4')).toBeTruthy()
    const selected = within(screen.getByRole('group', { name: 'Статистика выбранного периода' }))
    expect(selected.getByText('1')).toBeTruthy()
    expect(selected.getByText('Нет данных')).toBeTruthy()
    expect(screen.getByText(/Фильтры полученного списка: Все темы · Исследуется/)).toBeTruthy()
  })

  it.each(['single', 'all'])('показывает тему с одним обращением при support_level=%s', async support => {
    const user = userEvent.setup()
    const single = { ...topicListItem, topic: { ...topicListItem.topic, title: 'Новая одиночная тема', status: 'new' as const },
      stats: { ...topicListItem.stats, ticket_count: 1, support_level: 'single' as const } }
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([]).mockResolvedValue([single])
    render(<TopicList />)
    await screen.findByText(/Повторяющихся тем пока нет/)
    await user.selectOptions(screen.getByLabelText('Повторяемость'), support)
    submit()
    await screen.findByRole('button', { name: single.topic.title })
    expect(topics.mock.calls.at(-1)?.[1]).toEqual({ support_level: support })
  })

  it('не отправляет перевёрнутый период и сбрасывает форму в серверные значения по умолчанию', async () => {
    const user = userEvent.setup()
    const topics = vi.spyOn(api, 'topics').mockResolvedValue([topicListItem])
    render(<TopicList />)
    await screen.findByRole('article')
    fireEvent.change(screen.getByLabelText('Темы с даты'), { target: { value: '2026-11-02' } })
    fireEvent.change(screen.getByLabelText('Темы по дату'), { target: { value: '2026-11-01' } })
    submit()
    expect(screen.getByRole('alert').textContent).toContain('Начало периода позже окончания')
    expect(topics).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }))
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(2))
    expect(topics.mock.calls[1][1]).toEqual({ support_level: 'repeated' })
    expect(screen.getByLabelText('Темы с даты')).toHaveProperty('value', '')
  })

  it.each([422, 0])('сохраняет поля и последний список с его прежними фильтрами при ошибке %s', async status => {
    vi.spyOn(api, 'topics').mockResolvedValueOnce([topicListItem]).mockRejectedValue(new ApiError(status, {
      code: status === 422 ? 'INVALID_PERIOD' : 'NETWORK_UNAVAILABLE', message: 'Фильтры не загружены.',
    }))
    render(<TopicList />)
    await screen.findByRole('article')
    dates(); submit()
    await screen.findByText('Фильтры не загружены.')
    expect(screen.getByLabelText('Темы с даты')).toHaveProperty('value', period.date_from)
    expect(screen.getByRole('article')).toBeTruthy()
    expect(screen.getByText(/Фильтры полученного списка: Повторяющиеся · Все статусы, кроме объединённых · За всё время/)).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'Статистика выбранного периода' })).toBeNull()
    expect(screen.getByText('Показан последний полученный список. Обновление не подтверждено.')).toBeTruthy()
    expect(screen.queryByText(/По выбранным фильтрам тем нет/)).toBeNull()
  })

  it('различает успешный пустой фильтр и сброс периода', async () => {
    const user = userEvent.setup()
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([topicListItem]).mockResolvedValueOnce([])
      .mockResolvedValue([topicListItem])
    render(<TopicList />)
    await screen.findByRole('article')
    dates(); submit()
    await screen.findByText('По выбранным фильтрам тем нет. Сервер вернул пустой список.')
    expect(screen.queryByRole('article')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }))
    await screen.findByRole('article')
    expect(topics.mock.calls.at(-1)?.[1]).toEqual({ support_level: 'repeated' })
    expect(screen.queryByRole('group', { name: 'Статистика выбранного периода' })).toBeNull()
  })

  it('отменяет старый фильтр и игнорирует его запоздалый ответ', async () => {
    let finish: (items: TopicListItem[]) => void = () => { throw new Error('Запрос не создан') }
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([topicListItem])
      .mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
      .mockResolvedValue([{ ...topicListItem, topic: { ...topicListItem.topic, title: 'Актуальный фильтр' } }])
    render(<TopicList />)
    await screen.findByRole('article')
    fireEvent.change(screen.getByLabelText('Статус темы'), { target: { value: 'new' } }); submit()
    fireEvent.change(screen.getByLabelText('Статус темы'), { target: { value: 'needs_research' } }); submit()
    await screen.findByRole('button', { name: 'Актуальный фильтр' })
    expect(topics.mock.calls[1][0]?.aborted).toBe(true)
    await act(async () => finish([]))
    expect(screen.getByRole('button', { name: 'Актуальный фильтр' })).toBeTruthy()
    expect(screen.queryByText(/По выбранным фильтрам тем нет/)).toBeNull()
  })

  it('сохраняет карточку и черновик, когда новый фильтр исключает тему', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topics').mockResolvedValueOnce([topicDetail]).mockResolvedValue([])
    const topic = vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    const patch = vi.spyOn(api, 'saveDecision')
    render(<TopicList />)
    await user.click(await screen.findByRole('button', { name: topicDetail.topic.title }))
    await screen.findByLabelText('Причина решения')
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: 'Несохранённый черновик' } })
    fireEvent.change(screen.getByLabelText('Статус темы'), { target: { value: 'merged' } }); submit()
    await screen.findByText(/По выбранным фильтрам тем нет/)
    expect(screen.getByLabelText('Причина решения')).toHaveProperty('value', 'Несохранённый черновик')
    expect(screen.getByRole('region', { name: 'Карточка темы' })).toBeTruthy()
    expect(topic).toHaveBeenCalledTimes(1)
    expect(patch).not.toHaveBeenCalled()
  })

  it('после решения повторно применяет серверный фильтр и сохраняет подтверждение в открытой карточке', async () => {
    const user = userEvent.setup()
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([topicDetail]).mockResolvedValueOnce([topicDetail]).mockResolvedValue([])
    const body = { status: 'new' as const, owner: 'Синтетический владелец', reason: 'Синтетическая проверка', version: topicDetail.topic.version }
    const saved = savedDecision(topicDetail, body)
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(saved)
    const patch = vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    render(<TopicList />)
    await screen.findByRole('article')
    await user.selectOptions(screen.getByLabelText('Статус темы'), 'needs_research'); submit()
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: topicDetail.topic.title }))
    await screen.findByLabelText('Причина решения')
    await user.selectOptions(screen.getByLabelText('Статус решения'), 'new')
    fireEvent.change(screen.getByLabelText('Ответственный'), { target: { value: body.owner } })
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: body.reason } })
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    await screen.findByText(/По выбранным фильтрам тем нет/)
    expect(topics.mock.calls.at(-1)?.[1]).toEqual({ status: 'needs_research', support_level: 'repeated' })
    expect(patch).toHaveBeenCalledTimes(1)
    expect(screen.getByText('История решений (2)')).toBeTruthy()
  })

  it('после сохранения во время сброса фильтра получает свежий список, а не подписывает старый снимок как новый', async () => {
    const user = userEvent.setup()
    const body = { status: 'new' as const, owner: 'Синтетический владелец', reason: 'Проверка одновременного сброса', version: topicDetail.topic.version }
    const saved = savedDecision(topicDetail, body)
    let finishPatch: (value: typeof saved) => void = () => { throw new Error('PATCH не создан') }
    let finishReset: (items: TopicListItem[]) => void = () => { throw new Error('Сброс не начат') }
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([topicDetail]).mockResolvedValueOnce([topicDetail])
      .mockReturnValueOnce(new Promise(resolve => { finishReset = resolve })).mockResolvedValue([saved])
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(saved)
    vi.spyOn(api, 'saveDecision').mockReturnValue(new Promise(resolve => { finishPatch = resolve }))
    render(<TopicList />)
    await screen.findByRole('article')
    await user.selectOptions(screen.getByLabelText('Статус темы'), 'needs_research'); submit()
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: topicDetail.topic.title }))
    await screen.findByLabelText('Причина решения')
    await user.selectOptions(screen.getByLabelText('Статус решения'), 'new')
    fireEvent.change(screen.getByLabelText('Ответственный'), { target: { value: body.owner } })
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: body.reason } })
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }))
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(3))
    await act(async () => finishPatch(saved))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(4))
    expect(topics.mock.calls[3][1]).toEqual({ support_level: 'repeated' })
    expect(screen.getByText(/Фильтры полученного списка: Повторяющиеся · Все статусы, кроме объединённых · За всё время/)).toBeTruthy()
    await act(async () => finishReset([]))
    expect(within(screen.getByRole('article')).getByText('Новая')).toBeTruthy()
    expect(screen.getByLabelText('Причина решения')).toHaveProperty('value', body.reason)
  })

  it('проходит фильтры клавишей Tab и применяет их явным Enter', async () => {
    const user = userEvent.setup()
    const topics = vi.spyOn(api, 'topics').mockResolvedValue([topicListItem])
    render(<TopicList />)
    await screen.findByRole('article')
    const fields = [
      screen.getByRole('button', { name: 'Обновить список' }),
      screen.getByLabelText('Статус темы'), screen.getByLabelText('Повторяемость'),
      screen.getByLabelText('Темы с даты'), screen.getByLabelText('Темы по дату'),
      screen.getByRole('button', { name: 'Применить фильтры' }),
    ]
    for (const field of fields) { await user.tab(); expect(document.activeElement).toBe(field) }
    expect(topics).toHaveBeenCalledTimes(1)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(topics).toHaveBeenCalledTimes(2))
    await user.tab()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Сбросить фильтры' }))
  })
})
