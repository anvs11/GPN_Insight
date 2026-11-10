import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { DecisionUpdate, TopicDetail as Detail } from '../api-types'
import { savedDecision, topicDetail } from '../test-fixtures/detail'
import { TopicDetail } from './TopicDetail'
import { TopicList } from './TopicList'

const request: DecisionUpdate = { status: 'new', owner: 'Синтетический проверяющий', reason: 'Повторно проверить потребность', version: topicDetail.topic.version }
const saved = savedDecision(topicDetail, request)

function fill(value = request) {
  fireEvent.change(screen.getByLabelText('Статус решения'), { target: { value: value.status } })
  fireEvent.change(screen.getByLabelText('Ответственный'), { target: { value: value.owner } })
  fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: value.reason } })
}
async function openCard(detail = topicDetail) {
  render(<TopicDetail topicId={detail.topic.topic_id} onClose={() => {}} />)
  await screen.findByRole('region', { name: 'Решение владельца продукта' })
}
function expectDraft(value = request) {
  expect(screen.getByLabelText('Статус решения')).toHaveProperty('value', value.status)
  expect(screen.getByLabelText('Ответственный')).toHaveProperty('value', value.owner)
  expect(screen.getByLabelText('Причина решения')).toHaveProperty('value', value.reason)
}

describe('Сохранение решения владельца продукта', () => {
  it('требует непустые поля и не отправляет пробелы как решение', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    const patch = vi.spyOn(api, 'saveDecision')
    await openCard()
    fill({ ...request, reason: '  \n ' })
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Заполните ответственного и причину/)
    expect(patch).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Причина решения')).toHaveProperty('value', '  \n ')
  })

  it('блокирует двойной submit и подтверждает запись только после GET; обновляет карточку и строку списка', async () => {
    const user = userEvent.setup()
    let finishPatch: (detail: Detail) => void = () => { throw new Error('PATCH не создан') }
    let finishGet: (detail: Detail) => void = () => { throw new Error('GET не создан') }
    vi.spyOn(api, 'topics').mockResolvedValueOnce([topicDetail]).mockResolvedValue([saved])
    const topic = vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockReturnValueOnce(new Promise(resolve => { finishGet = resolve }))
    const patch = vi.spyOn(api, 'saveDecision').mockReturnValue(new Promise(resolve => { finishPatch = resolve }))
    render(<TopicList />)
    await user.click(await screen.findByRole('button', { name: topicDetail.topic.title }))
    await screen.findByLabelText('Причина решения')
    fill()
    const form = screen.getByLabelText('Причина решения').closest('form')!
    fireEvent.submit(form); fireEvent.submit(form)
    expect(patch).toHaveBeenCalledTimes(1)
    expect(patch).toHaveBeenCalledWith(topicDetail.topic.topic_id, request, expect.any(AbortSignal))
    expect(screen.getByRole('button', { name: 'Сохраняем решение…' })).toHaveProperty('disabled', true)
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    await act(async () => finishPatch(saved))
    expect(screen.getByRole('button', { name: 'Проверяем карточку…' })).toHaveProperty('disabled', true)
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    await act(async () => finishGet(saved))
    await screen.findByText(/Сохранение подтверждено повторным чтением: «Новая», версия 3/)
    expect(topic).toHaveBeenCalledTimes(2)
    expect(patch).toHaveBeenCalledTimes(1)
    const row = within(screen.getByRole('article'))
    expect(row.getByText(request.owner)).toBeTruthy()
    expect(row.getByText('Новая')).toBeTruthy()
    expect(screen.getByText('История решений (2)')).toBeTruthy()
    expect(screen.getByText('Причина: Сохранённая причина решения')).toBeTruthy()
    expectDraft()
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
  })

  it.each([
    ['new', 'new'], ['new', 'needs_research'], ['needs_research', 'new'], ['needs_research', 'needs_research'],
  ] as const)('сохраняет поддерживаемый переход %s → %s с увеличением версии', async (previous, next) => {
    const user = userEvent.setup()
    const before = { ...topicDetail, topic: { ...topicDetail.topic, status: previous } }
    const body = { ...request, status: next }
    const after = savedDecision(before, body)
    vi.spyOn(api, 'topic').mockResolvedValueOnce(before).mockResolvedValue(after)
    const patch = vi.spyOn(api, 'saveDecision').mockResolvedValue(after)
    await openCard(before)
    expect(within(screen.getByLabelText('Статус решения')).getAllByRole('option')).toHaveLength(6)
    fill(body)
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    expect(patch.mock.calls[0][1]).toEqual(body)
    expect(screen.getByText('История решений (2)')).toBeTruthy()
  })

  it('при 409 перечитывает карточку, сохраняет черновик и требует явного принятия новой версии', async () => {
    const user = userEvent.setup()
    const other = savedDecision(topicDetail, { ...request, status: 'needs_research', owner: 'Другой владелец', reason: 'Решение другой вкладки' }, 'synthetic-other')
    const retry = { ...request, version: other.topic.version }
    const final = savedDecision(other, retry, 'synthetic-retry')
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValueOnce(other).mockResolvedValue(final)
    const patch = vi.spyOn(api, 'saveDecision').mockRejectedValueOnce(new ApiError(409, { code: 'VERSION_CONFLICT', message: 'Тема уже изменена.' })).mockResolvedValue(final)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('HTTP 409 · VERSION_CONFLICT')
    await screen.findByText('Причина: Решение другой вкладки')
    expectDraft()
    expect(screen.queryByText(/Сохранение подтверждено/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
    expect(patch).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Использовать версию 3 для нового сохранения' }))
    expectDraft()
    expect(patch).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением: «Новая», версия 4/)
    expect(patch.mock.calls[1][1]).toEqual(retry)
    expect(screen.getByText('История решений (3)')).toBeTruthy()
  })

  it('не принимает старую версию, если после 409 новое чтение не удалось', async () => {
    const user = userEvent.setup()
    const other = savedDecision(topicDetail, { ...request, owner: 'Другой владелец' })
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail)
      .mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Не удалось получить новую версию.' })).mockResolvedValue(other)
    const patch = vi.spyOn(api, 'saveDecision').mockRejectedValue(new ApiError(409, { code: 'VERSION_CONFLICT', message: 'Конфликт версии.' }))
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('Не удалось получить новую версию.')
    expect(screen.queryByRole('button', { name: /Использовать версию/ })).toBeNull()
    expectDraft()
    await user.click(screen.getByRole('button', { name: 'Проверить карточку без повторного сохранения' }))
    await screen.findByRole('button', { name: 'Использовать версию 3 для нового сохранения' })
    expect(patch).toHaveBeenCalledTimes(1)
  })

  it.each([422, 404])('сохраняет все поля при HTTP %s и не сообщает об успехе', async status => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    const patch = vi.spyOn(api, 'saveDecision').mockRejectedValue(new ApiError(status, { code: status === 404 ? 'TOPIC_NOT_FOUND' : 'VALIDATION_ERROR', message: 'Запрос отклонён сервером.' }))
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('Запрос отклонён сервером.')
    expectDraft()
    expect(screen.queryByText(/Сохранение подтверждено/)).toBeNull()
    expect(patch).toHaveBeenCalledTimes(1)
    if (status === 404) expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
  })

  it('подтверждает потерянный ответ PATCH через GET, не создавая повторного решения', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(saved)
    const patch = vi.spyOn(api, 'saveDecision').mockRejectedValue(new ApiError(0, { code: 'REQUEST_TIMEOUT', message: 'Ответ PATCH потерян.' }))
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    expect(patch).toHaveBeenCalledTimes(1)
    expectDraft()
  })

  it.each([0, 503])('при неподтверждённом PATCH с HTTP %s требует проверки версии перед новой попыткой', async status => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValueOnce(topicDetail).mockResolvedValue(saved)
    const patch = vi.spyOn(api, 'saveDecision').mockRejectedValueOnce(new ApiError(status, {
      code: status === 503 ? 'DATABASE_UNAVAILABLE' : 'NETWORK_UNAVAILABLE', message: 'Сохранение сервером не подтверждено.',
    })).mockResolvedValue(saved)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('HTTP 200 · DECISION_NOT_CONFIRMED')
    expectDraft()
    expect(patch).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
    await user.click(screen.getByRole('button', { name: 'Использовать версию 2 для нового сохранения' }))
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    expect(patch.mock.calls[1][1].version).toBe(2)
  })

  it('после успешного PATCH и отказа GET повторяет только проверку, сохраняя черновик', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail)
      .mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Повторное чтение недоступно.' })).mockResolvedValue(saved)
    const patch = vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('Повторное чтение недоступно.')
    expectDraft()
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Использовать версию/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
    const edited = { ...request, reason: 'Уточнённый черновик после ошибки чтения' }
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: edited.reason } })
    await user.click(screen.getByRole('button', { name: 'Проверить карточку без повторного сохранения' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    expectDraft(edited)
    expect(screen.getByText(/Новые значения формы пока не отправлены/)).toBeTruthy()
    expect(patch).toHaveBeenCalledTimes(1)
  })

  it.each(['missing_event', 'changed_history', 'wrong_status'] as const)('не подтверждает повреждённое повторное чтение: %s', async issue => {
    const user = userEvent.setup()
    const broken = structuredClone(saved)
    if (issue === 'missing_event') broken.history = topicDetail.history
    if (issue === 'changed_history') broken.history[0].reason = 'Прежняя причина незаметно заменена'
    if (issue === 'wrong_status') broken.topic.status = 'needs_research'
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(broken)
    vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('HTTP 200 · DECISION_NOT_CONFIRMED')
    expectDraft()
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
    expect(screen.queryByRole('button', { name: /Использовать версию/ })).toBeNull()
  })

  it('не принимает некорректный ответ PATCH за запись решения', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    vi.spyOn(api, 'saveDecision').mockResolvedValue(topicDetail)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('HTTP 200 · INVALID_DECISION_RESPONSE')
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    expectDraft()
  })

  it('отличает подтверждённое событие от более позднего решения другой вкладки', async () => {
    const user = userEvent.setup()
    const later = savedDecision(saved, { ...request, status: 'needs_research', owner: 'Следующий владелец', reason: 'Решение после нашего', version: saved.topic.version }, 'synthetic-later')
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(later)
    vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Запись вашего решения найдена в истории версии 3, но после этого карточка изменилась/)
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    expect(screen.getByText('Причина: Решение после нашего')).toBeTruthy()
    expectDraft()
    expect(screen.getByRole('button', { name: 'Использовать версию 4 для нового сохранения' })).toBeTruthy()
  })

  it('сохраняет черновик при обычном обновлении карточки и не меняет версию формы автоматически', async () => {
    const user = userEvent.setup()
    const other = savedDecision(topicDetail, { ...request, owner: 'Другой владелец' })
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(other)
    const patch = vi.spyOn(api, 'saveDecision')
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Обновить карточку' }))
    await screen.findByRole('button', { name: 'Использовать версию 3 для нового сохранения' })
    expectDraft()
    expect(patch).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
  })

  it('запрещает изменение merged и сохраняет видимую ссылку на основную тему', async () => {
    const detail: Detail = { ...topicDetail, topic: { ...topicDetail.topic, status: 'merged', canonical_topic_id: 'synthetic-canonical' } }
    vi.spyOn(api, 'topic').mockResolvedValue(detail)
    const patch = vi.spyOn(api, 'saveDecision')
    await openCard(detail)
    expect(screen.getByText(/Решение нужно менять в основной теме synthetic-canonical/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toHaveProperty('disabled', true)
    expect(patch).not.toHaveBeenCalled()
  })

  it('не откатывает строку списка запоздалым обновлением, начатым до сохранения решения', async () => {
    const user = userEvent.setup()
    let finishList: (items: Detail[]) => void = () => { throw new Error('Запрос списка не создан') }
    const topics = vi.spyOn(api, 'topics').mockResolvedValueOnce([topicDetail])
      .mockReturnValueOnce(new Promise(resolve => { finishList = resolve })).mockResolvedValue([saved])
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValue(saved)
    vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    render(<TopicList />)
    await user.click(await screen.findByRole('button', { name: topicDetail.topic.title }))
    await screen.findByLabelText('Причина решения')
    await user.click(screen.getByRole('button', { name: 'Обновить список' }))
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    await act(async () => finishList([topicDetail]))
    const row = within(screen.getByRole('article'))
    expect(row.getByText(request.owner)).toBeTruthy()
    expect(row.getByText('Новая')).toBeTruthy()
    expect(topics.mock.calls[1][0]?.aborted).toBe(true)
    expect(screen.getByRole('button', { name: 'Обновить список' })).toHaveProperty('disabled', false)
  })

  it('не заменяет подтверждённую карточку запоздалым ручным GET', async () => {
    const user = userEvent.setup()
    let finishOld: (detail: Detail) => void = () => { throw new Error('Старый GET не создан') }
    const topic = vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail)
      .mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Чтение после PATCH недоступно.' }))
      .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
      .mockResolvedValue(saved)
    vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText('Чтение после PATCH недоступно.')
    await user.click(screen.getByRole('button', { name: 'Обновить карточку' }))
    await user.click(screen.getByRole('button', { name: 'Проверить карточку без повторного сохранения' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    await act(async () => finishOld(topicDetail))
    expect(screen.getByText('Версия темы').nextElementSibling?.textContent).toBe('3')
    expect(screen.getByText('История решений (2)')).toBeTruthy()
    expect(topic.mock.calls[2][1]?.aborted).toBe(true)
    expectDraft()
  })

  it('не показывает прежнее подтверждение как актуальное во время обновления и после чужого решения', async () => {
    const user = userEvent.setup()
    let finish: (detail: Detail) => void = () => { throw new Error('Обновление не создано') }
    const later = savedDecision(saved, { ...request, status: 'needs_research', owner: 'Другой владелец', version: 3 }, 'synthetic-later-refresh')
    vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail).mockResolvedValueOnce(saved)
      .mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    vi.spyOn(api, 'saveDecision').mockResolvedValue(saved)
    await openCard()
    fill()
    await user.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    await user.click(screen.getByRole('button', { name: 'Обновить карточку' }))
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    await act(async () => finish(later))
    expect(screen.queryByText(/Сохранение подтверждено повторным чтением/)).toBeNull()
    expect(screen.getByText('Версия темы').nextElementSibling?.textContent).toBe('4')
    expect(screen.getByRole('button', { name: 'Использовать версию 4 для нового сохранения' })).toBeTruthy()
    expectDraft()
  })
})
