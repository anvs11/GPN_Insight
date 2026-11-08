import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { TopicChangeEvent, TopicDetail as Detail } from '../api-types'
import { passportChange, passportDetail, passportRequest, savedPassport } from '../test-fixtures/passport'
import { TopicDetail } from './TopicDetail'
import { TopicList } from './TopicList'

const request = passportRequest(), saved = savedPassport(passportDetail, request), event = passportChange(passportDetail, request)
function wire(before = passportDetail) {
  const topic = vi.spyOn(api, 'topic').mockResolvedValue(before)
  const changes = vi.spyOn(api, 'topicChanges').mockResolvedValue([])
  const patch = vi.spyOn(api, 'savePassport')
  return { topic, changes, patch }
}
async function open() {
  render(<TopicDetail topicId={passportDetail.topic.topic_id} onClose={() => {}} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Редактировать паспорт и посмотреть историю' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Сохранить паспорт' })).toHaveProperty('disabled', false))
}
function fill() {
  for (const [label, value] of [['Название темы', request.title], ['Автор изменения паспорта', request.author], ['Причина изменения паспорта', request.reason]] as const) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  }
  fireEvent.click(screen.getByLabelText('Границы паспорта проверены'))
}
function submit() { fireEvent.submit(screen.getByRole('form', { name: 'Форма паспорта' })) }

describe('Редактор паспорта', () => {
  it('подтверждает запись только после GET карточки и истории, блокирует повторы и сохраняет черновик решения', async () => {
    const { topic, changes, patch } = wire()
    topic.mockResolvedValueOnce(passportDetail).mockResolvedValue(saved)
    let finishPatch: (value: Detail) => void = () => { throw Error('PATCH не начат') }
    let finishHistory: (value: TopicChangeEvent[]) => void = () => { throw Error('GET истории не начат') }
    changes.mockResolvedValueOnce([]).mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(resolve => { finishHistory = resolve }))
    patch.mockReturnValue(new Promise(resolve => { finishPatch = resolve }))
    await open()
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: 'Черновик решения' } })
    fill(); submit(); submit()
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1))
    expect(patch).toHaveBeenCalledWith(passportDetail.topic.topic_id, request, expect.any(AbortSignal))
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: 'Закрыть карточку' })).toHaveProperty('disabled', true)
    await act(async () => finishPatch(saved))
    expect(screen.queryByText(/Сохранение паспорта подтверждено/)).toBeNull()
    await act(async () => finishHistory([event]))
    await screen.findByText(/Сохранение паспорта подтверждено/)
    expect(screen.getByLabelText('Причина решения')).toHaveProperty('value', 'Черновик решения')
    expect(screen.getByLabelText('Причина изменения паспорта')).toHaveProperty('value', request.reason)
    expect(screen.getByText('История изменений темы (1)')).toBeTruthy()
    expect(screen.getByText('История решений (1)')).toBeTruthy()
    submit()
    expect(patch).toHaveBeenCalledTimes(1)
  })
  it('проверяет обязательные поля и позволяет добавить inclusion/exclusion с выбранными цитатами', async () => {
    const before = structuredClone(passportDetail)
    before.topic.passport.inclusion_examples = []
    const { patch } = wire(before)
    await open()
    fill(); submit()
    expect(await screen.findByText(/Заполните название, сценарий/)).toBeTruthy()
    expect(patch).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Добавить пример включения' }))
    fireEvent.change(screen.getByLabelText('Текст примера включения 1'), { target: { value: 'Проверенный пример' } })
    const inclusion = within(screen.getByRole('group', { name: 'Пример включения 1' }))
    await userEvent.click(inclusion.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: 'Добавить пример исключения' }))
    fireEvent.change(screen.getByLabelText('Текст примера исключения 1'), { target: { value: 'Другое намерение, проверенное человеком' } })
    await userEvent.click(within(screen.getByRole('group', { name: 'Пример исключения 1' })).getByRole('checkbox'))
    patch.mockRejectedValue(new ApiError(422, { code: 'INVALID_EVIDENCE', message: 'Отказ тестового сервера.' }))
    submit()
    await screen.findByText('Отказ тестового сервера.')
    const sent = patch.mock.calls[0][1]
    expect(sent.passport.inclusion_examples[0].evidence_refs).toEqual(before.topic.evidence)
    expect(sent.passport.exclusion_examples[0].evidence_refs).toEqual(before.topic.evidence)
    expect(sent.passport.scope_confirmed).toBe(true)
    expect(sent.passport).not.toHaveProperty('product_id')
    expect(sent.passport).not.toHaveProperty('version')
    await userEvent.click(screen.getByRole('button', { name: 'Удалить пример исключения 1' }))
    expect(screen.queryByLabelText('Текст примера исключения 1')).toBeNull()
  })
  it.each([404, 422])('сохраняет все поля при HTTP %s без повторной записи', async status => {
    const { patch, topic } = wire()
    patch.mockRejectedValue(new ApiError(status, { code: 'REJECTED', message: 'Паспорт отклонён.' }))
    await open(); fill(); submit()
    await screen.findByText('Паспорт отклонён.')
    expect(screen.getByLabelText('Название темы')).toHaveProperty('value', request.title)
    expect(screen.getByLabelText('Причина изменения паспорта')).toHaveProperty('value', request.reason)
    expect(screen.getByLabelText('Текст примера включения 1')).toHaveProperty('value', 'Проверенный пример')
    expect(patch).toHaveBeenCalledTimes(1)
    expect(topic).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/Сохранение паспорта подтверждено/)).toBeNull()
  })
  it('после 409 перечитывает обе версии, сохраняет черновик и требует явного согласия для новой записи', async () => {
    const { topic, changes, patch } = wire()
    const otherRequest = { ...request, title: 'Правка другого пользователя', author: 'Другой аналитик' }
    const other = savedPassport(passportDetail, otherRequest), otherEvent = passportChange(passportDetail, otherRequest, 'synthetic-other')
    let current = passportDetail, history: TopicChangeEvent[] = []
    topic.mockImplementation(async () => current)
    changes.mockImplementation(async () => history)
    patch.mockImplementationOnce(async () => {
      current = other; history = [otherEvent]
      throw new ApiError(409, { code: 'VERSION_CONFLICT', message: 'Версии изменились.' })
    }).mockImplementation(async (_id, body) => {
      const before = current
      current = savedPassport(before, body); history = [...history, passportChange(before, body)]
      return current
    })
    await open(); fill(); submit()
    await screen.findByRole('button', { name: 'Использовать версии 3/2 для новой правки паспорта' })
    expect(patch).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Название темы')).toHaveProperty('value', request.title)
    expect(screen.getByRole('button', { name: 'Сохранить паспорт' })).toHaveProperty('disabled', true)
    await userEvent.click(screen.getByRole('button', { name: 'Использовать версии 3/2 для новой правки паспорта' }))
    expect(patch).toHaveBeenCalledTimes(1)
    submit()
    await screen.findByText(/Сохранение паспорта подтверждено/)
    expect(patch.mock.calls[1][1]).toMatchObject({ version: 3, passport_version: 2, title: request.title, reason: request.reason })
    expect(history).toHaveLength(2)
  })
  it.each([0, 200, 503])('восстанавливает потерянный/неопределённый ответ %s только GET, без второго PATCH', async status => {
    const { topic, changes, patch } = wire()
    let current = passportDetail, history: TopicChangeEvent[] = []
    topic.mockImplementation(async () => current); changes.mockImplementation(async () => history)
    patch.mockImplementation(async () => {
      current = saved; history = [event]
      throw new ApiError(status, { code: 'UNKNOWN_RESULT', message: 'Ответ потерян.' })
    })
    await open(); fill(); submit()
    await screen.findByText(/Сохранение паспорта подтверждено/)
    expect(patch).toHaveBeenCalledTimes(1)
    expect(topic).toHaveBeenCalledTimes(2)
    expect(screen.getByLabelText('Автор изменения паспорта')).toHaveProperty('value', request.author)
    submit()
    expect(patch).toHaveBeenCalledTimes(1)
  })
  it('после принятого PATCH и отказа GET истории проверяет сохранение без повторной отправки', async () => {
    const { topic, changes, patch } = wire()
    topic.mockResolvedValueOnce(passportDetail).mockResolvedValue(saved); patch.mockResolvedValue(saved)
    changes.mockResolvedValueOnce([]).mockResolvedValueOnce([])
      .mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'История недоступна.' })).mockResolvedValue([event])
    await open(); fill(); submit()
    await screen.findByText('История недоступна.')
    expect(screen.queryByText(/Сохранение паспорта подтверждено/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Использовать версии/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Сохранить паспорт' })).toHaveProperty('disabled', true)
    await userEvent.click(screen.getByRole('button', { name: 'Проверить паспорт без повторной записи' }))
    await screen.findByText(/Сохранение паспорта подтверждено/)
    expect(patch).toHaveBeenCalledTimes(1)
  })
  it('не подтверждает запись без соответствующего события и блокирует повторный PATCH', async () => {
    const { topic, patch } = wire()
    topic.mockResolvedValueOnce(passportDetail).mockResolvedValue(saved); patch.mockResolvedValue(saved)
    await open(); fill(); submit()
    await screen.findByText(/Карточка и история не подтвердили/)
    expect(screen.queryByText(/Сохранение паспорта подтверждено/)).toBeNull()
    submit(); expect(patch).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: /Использовать версии/ })).toBeNull()
    expect(screen.getByLabelText('Причина изменения паспорта')).toHaveProperty('value', request.reason)
  })
  it('не отправляет PATCH при отказе предварительного чтения истории', async () => {
    const { changes, patch } = wire()
    changes.mockResolvedValueOnce([]).mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Нет истории.' }))
    await open(); fill(); submit()
    await screen.findByText('Нет истории.')
    expect(patch).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Автор изменения паспорта')).toHaveProperty('value', request.author)
  })
  it('сохраняет черновик при обновлении карточки и не принимает новые версии незаметно', async () => {
    const { topic, patch } = wire()
    topic.mockResolvedValueOnce(passportDetail).mockResolvedValue(saved)
    await open(); fill()
    await userEvent.click(screen.getByRole('button', { name: 'Обновить карточку' }))
    await screen.findByRole('button', { name: 'Использовать версии 3/2 для новой правки паспорта' })
    expect(screen.getByLabelText('Причина изменения паспорта')).toHaveProperty('value', request.reason)
    expect(patch).not.toHaveBeenCalled()
  })
  it('не отправляет неподтверждённую цитату из текущего pending источника', async () => {
    const before = structuredClone(passportDetail); before.sources[0].requires_analysis = true
    const { patch } = wire(before)
    await open(); fill(); submit()
    await screen.findByText(/Заполните название, сценарий/)
    expect(screen.getByText(/текущий фрагмент не подтверждён/)).toBeTruthy()
    expect(patch).not.toHaveBeenCalled()
  })
  it('читает отдельный источник примера вне списка активных источников темы', async () => {
    const before = structuredClone(passportDetail)
    before.topic.passport.exclusion_examples = [{ text: 'Исключение', evidence_refs: [{ ...before.topic.evidence[0], source_id: 'SYNTHETIC-EXTRA' }] }]
    const extra = structuredClone(before.sources[0]); extra.ticket.source_id = 'SYNTHETIC-EXTRA'; extra.document!.source_id = 'SYNTHETIC-EXTRA'
    const source = vi.spyOn(api, 'source').mockResolvedValue(extra)
    wire(before)
    await open()
    await waitFor(() => expect(source).toHaveBeenCalledWith('SYNTHETIC-EXTRA', expect.any(AbortSignal)))
    await waitFor(() => expect(screen.queryByText(/Проверяем источники примеров/)).toBeNull())
    expect(within(screen.getByRole('group', { name: 'Пример исключения 1' })).queryByText(/не подтверждён/)).toBeNull()
  })
  it('запрещает изменение объединённой темы', async () => {
    const before: Detail = { ...passportDetail, topic: { ...passportDetail.topic, status: 'merged', canonical_topic_id: 'synthetic-main' } }
    const { patch } = wire(before)
    render(<TopicDetail topicId={before.topic.topic_id} onClose={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Редактировать паспорт и посмотреть историю' }))
    await screen.findByText(/Паспорт меняется в основной теме synthetic-main/)
    expect(screen.getByLabelText('Автор изменения паспорта')).toHaveProperty('disabled', false)
    expect(screen.getByLabelText('Автор изменения паспорта').closest('fieldset')).toHaveProperty('disabled', true)
    submit(); expect(patch).not.toHaveBeenCalled()
  })
  it('сохраняет паспортный черновик, когда фильтр исключает открытую тему', async () => {
    wire()
    vi.spyOn(api, 'topics').mockResolvedValueOnce([passportDetail]).mockResolvedValue([])
    render(<TopicList />)
    await userEvent.click(await screen.findByRole('button', { name: passportDetail.topic.title }))
    await userEvent.click(await screen.findByRole('button', { name: 'Редактировать паспорт и посмотреть историю' }))
    fireEvent.change(screen.getByLabelText('Причина изменения паспорта'), { target: { value: 'Несохранённая правка' } })
    fireEvent.change(screen.getByLabelText('Статус темы'), { target: { value: 'merged' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Фильтры тем' }))
    await screen.findByText(/По выбранным фильтрам тем нет/)
    expect(screen.getByLabelText('Причина изменения паспорта')).toHaveProperty('value', 'Несохранённая правка')
  })
})

describe('Регрессии совместного редактирования', () => {
  it('не сохраняет одновременно паспорт и решение даже до следующего рендера', async () => {
    const { patch } = wire()
    patch.mockReturnValue(new Promise(() => {}))
    const decision = vi.spyOn(api, 'saveDecision')
    await open(); fill()
    fireEvent.change(screen.getByLabelText('Ответственный'), { target: { value: 'Владелец' } })
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: 'Черновик решения' } })
    act(() => {
      submit()
      fireEvent.submit(within(screen.getByRole('region', { name: 'Решение владельца продукта' })).getByText('Сохранить решение').closest('form')!)
    })
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1))
    expect(decision).not.toHaveBeenCalled()
  })
  it('не показывает прежнее подтверждение при неподтверждённом обновлении истории и сохраняет старые события', async () => {
    const { topic, changes, patch } = wire()
    topic.mockResolvedValueOnce(passportDetail).mockResolvedValue(saved); patch.mockResolvedValue(saved)
    changes.mockResolvedValueOnce([]).mockResolvedValueOnce([]).mockResolvedValueOnce([event]).mockResolvedValueOnce([]).mockResolvedValue([event])
    await open(); fill(); submit()
    await screen.findByText(/Сохранение паспорта подтверждено/)
    await userEvent.click(screen.getByRole('button', { name: 'Обновить историю изменений' }))
    await screen.findByText(/Прежняя история изменений не подтверждена/)
    expect(screen.queryByText(/Сохранение паспорта подтверждено/)).toBeNull()
    expect(screen.getByText('История изменений темы (1)')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Проверить паспорт без повторной записи' }))
    await screen.findByText(/Сохранение паспорта подтверждено/)
    expect(patch).toHaveBeenCalledTimes(1)
  })
  it('после отказа начального чтения истории позволяет повторить только GET и сохраняет поля', async () => {
    const { changes, patch } = wire()
    changes.mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Нет истории изменений.' })).mockResolvedValue([])
    render(<TopicDetail topicId={passportDetail.topic.topic_id} onClose={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Редактировать паспорт и посмотреть историю' }))
    await screen.findByText('Нет истории изменений.')
    fill()
    expect(screen.getByRole('button', { name: 'Сохранить паспорт' })).toHaveProperty('disabled', true)
    await userEvent.click(screen.getByRole('button', { name: 'Обновить историю изменений' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Сохранить паспорт' })).toHaveProperty('disabled', false))
    expect(screen.getByLabelText('Причина изменения паспорта')).toHaveProperty('value', request.reason)
    expect(patch).not.toHaveBeenCalled()
  })
  it('сохраняет пример при 404 отдельного источника и восстанавливает проверку цитат без записи', async () => {
    const before = structuredClone(passportDetail)
    before.topic.passport.inclusion_examples[0].evidence_refs[0].source_id = 'SYNTHETIC-EXTRA'
    const extra = structuredClone(before.sources[0]); extra.ticket.source_id = 'SYNTHETIC-EXTRA'; extra.document!.source_id = 'SYNTHETIC-EXTRA'
    const source = vi.spyOn(api, 'source').mockRejectedValueOnce(new ApiError(404, { code: 'SOURCE_NOT_FOUND', message: 'Источник не найден.' })).mockResolvedValue(extra)
    const { patch } = wire(before)
    await open(); fill()
    await screen.findByText(/Не удалось проверить источники: SYNTHETIC-EXTRA/)
    expect(screen.getByLabelText('Текст примера включения 1')).toHaveProperty('value', 'Проверенный пример')
    submit()
    expect(patch).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить проверку цитат' }))
    await waitFor(() => expect(source).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText(/Не удалось проверить источники:/)).toBeNull())
    expect(patch).not.toHaveBeenCalled()
  })
})
