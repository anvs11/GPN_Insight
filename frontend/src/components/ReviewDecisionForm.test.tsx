/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useCallback, useState } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { ResolveReviewRequest, ReviewItem } from '../api-types'
import { reviewRequestHash } from '../review-verification'
import { attachRequest, resolvedContext, reviewTarget } from '../test-fixtures/review'
import { reviewItem } from '../test-fixtures/import'
import { ReviewDecisionForm } from './ReviewDecisionForm'
import { ReviewPanel } from './ReviewPanel'

function Harness({ initial = reviewItem, stale = false }: { initial?: ReviewItem; stale?: boolean }) {
  const [item, setItem] = useState(initial)
  const onRead = useCallback((value: ReviewItem) => setItem(value), [])
  const onBusyChange = useCallback(() => true, [])
  return <ReviewDecisionForm item={item} stale={stale} onRead={onRead} onBusyChange={onBusyChange} />
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(api, 'review').mockReset().mockResolvedValue(reviewItem)
  vi.spyOn(api, 'reviewHistory').mockReset().mockResolvedValue([reviewItem])
  vi.spyOn(api, 'topicCatalog').mockResolvedValue([reviewTarget])
})
async function form(action: 'attach' | 'insufficient_data' = 'attach') {
  await screen.findByLabelText('Автор решения Review')
  if (action === 'attach') fireEvent.change(screen.getByLabelText('Существующая тема'), { target: { value: 'synthetic-topic' } })
  else fireEvent.click(screen.getByRole('button', { name: 'Недостаточно данных' }))
  fireEvent.change(screen.getByLabelText('Автор решения Review'), { target: { value: 'Синтетический аналитик' } })
  fireEvent.change(screen.getByLabelText('Причина решения Review'), { target: { value: 'Проверены источники' } })
}
function submit() { fireEvent.submit(screen.getByRole('form')) }
async function prepareSaved(request: ResolveReviewRequest = attachRequest) {
  const after = resolvedContext(request, await reviewRequestHash('synthetic-review', request))
  vi.spyOn(api, 'review').mockReset().mockResolvedValueOnce(reviewItem).mockResolvedValue(after.review)
  vi.spyOn(api, 'reviewHistory').mockReset().mockResolvedValueOnce([reviewItem]).mockResolvedValue(after.history)
  vi.spyOn(api, 'topicCatalog').mockResolvedValueOnce([reviewTarget]).mockResolvedValue(after.topics)
  return after
}
describe('Решения Review: два действия без потери черновика', () => {
  it('attach отправляет точную тему/версии и подтверждается только повторным GET с историей', async () => {
    const after = await prepareSaved()
    const save = vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<Harness />); await form()
    expect(screen.getByRole('complementary', { name: 'Выбранная тема' })).toBeTruthy()
    expect(save).not.toHaveBeenCalled()
    submit()
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0][0]).toBe('synthetic-review')
    expect(save.mock.calls[0][1]).toEqual(attachRequest)
    expect(api.review).toHaveBeenCalledTimes(2)
    expect(api.reviewHistory).toHaveBeenCalledTimes(2)
    expect(screen.getByLabelText('Причина решения Review')).toHaveProperty('value', 'Проверены источники')
  })
  it('insufficient_data не передаёт topic_id/proposed_topic и не требует выбора темы', async () => {
    const request = { version: 1, action: 'insufficient_data' as const, author: attachRequest.author, reason: attachRequest.reason, topic_versions: {} }
    const after = await prepareSaved(request)
    const save = vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<Harness />); await form('insufficient_data'); submit()
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
    expect(save.mock.calls[0][1]).toEqual(request)
    expect(screen.queryByLabelText('Существующая тема')).toBeNull()
    expect(screen.getByRole('button', { name: 'Создать новую тему' })).toBeTruthy()
  })
  it('не отправляет пустые автора/причину и не выбирает тему вместо человека', async () => {
    const save = vi.spyOn(api, 'resolveReview')
    render(<Harness />)
    await screen.findByLabelText('Автор решения Review')
    expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', true)
    fireEvent.change(screen.getByLabelText('Существующая тема'), { target: { value: 'synthetic-topic' } })
    submit()
    await screen.findByText('Укажите непустые поля автора и причины решения.')
    expect(save).not.toHaveBeenCalled()
  })
  it('блокирует двойной submit до следующего рендера, включая ожидание хеша', async () => {
    const after = await prepareSaved()
    let finish: (value: ReviewItem) => void = () => {}
    const save = vi.spyOn(api, 'resolveReview').mockReturnValue(new Promise(resolve => { finish = resolve }))
    render(<Harness />); await form()
    submit(); submit()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', true)
    await act(async () => { finish(after.review) })
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
  })
  it('потерянный ответ после реальной записи проверяется GET без второго POST', async () => {
    await prepareSaved()
    const save = vi.spyOn(api, 'resolveReview').mockRejectedValue(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Ответ потерян.' }))
    render(<Harness />); await form(); submit()
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
    expect(save).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Ответ потерян.')).toBeNull()
  })
  it('принятый POST + отказ GET не показывает успех и допускает только повторное чтение', async () => {
    const after = await prepareSaved()
    vi.spyOn(api, 'review').mockReset().mockResolvedValueOnce(reviewItem).mockRejectedValueOnce(new ApiError(503, { code: 'DATABASE_UNAVAILABLE', message: 'Чтение недоступно.' })).mockResolvedValue(after.review)
    const save = vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<Harness />); await form(); submit()
    await screen.findByText('Чтение недоступно.')
    expect(screen.queryByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Повторить ту же команду' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Проверить Review без повторной записи' }))
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
    expect(save).toHaveBeenCalledTimes(1)
  })
  it('не разрешает повтор команды по старому GET, если проверка после сетевой ошибки тоже не удалась', async () => {
    vi.spyOn(api, 'review').mockReset().mockResolvedValueOnce(reviewItem).mockRejectedValue(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Сети нет.' }))
    const save = vi.spyOn(api, 'resolveReview').mockRejectedValue(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Сети нет.' }))
    render(<Harness />); await form(); submit()
    await screen.findAllByText('Сети нет.')
    expect(screen.queryByRole('button', { name: 'Повторить ту же команду' })).toBeNull()
    expect(save).toHaveBeenCalledTimes(1)
  })
  it('после успешного GET открытого Review допускает только явный повтор исходной команды', async () => {
    const after = resolvedContext(attachRequest, await reviewRequestHash('synthetic-review', attachRequest))
    const save = vi.spyOn(api, 'resolveReview').mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Соединение потеряно до сервера.' }))
      .mockImplementationOnce(async () => {
        vi.spyOn(api, 'review').mockReset().mockResolvedValue(after.review)
        vi.spyOn(api, 'reviewHistory').mockReset().mockResolvedValue(after.history)
        vi.spyOn(api, 'topicCatalog').mockResolvedValue(after.topics)
        return after.review
      })
    render(<Harness />); await form(); submit()
    await screen.findByRole('button', { name: 'Повторить ту же команду' })
    expect(save).toHaveBeenCalledTimes(1)
    fireEvent.change(screen.getByLabelText('Причина решения Review'), { target: { value: 'Новый черновик пока не отправлен' } })
    fireEvent.click(screen.getByRole('button', { name: 'Повторить ту же команду' }))
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
    expect(save).toHaveBeenCalledTimes(2)
    expect(save.mock.calls[1][1]).toEqual(save.mock.calls[0][1])
    expect(screen.getByLabelText('Причина решения Review')).toHaveProperty('value', 'Новый черновик пока не отправлен')
    expect(screen.getByText('Новые значения черновика не отправлены. Подтверждена исходная команда, указанная выше.')).toBeTruthy()
  })
  it('HTTP 409 сохраняет черновик и требует явного принятия новых версий', async () => {
    const newer = structuredClone(reviewTarget); newer.topic.version = 4
    vi.spyOn(api, 'topicCatalog').mockResolvedValueOnce([reviewTarget]).mockResolvedValue([newer])
    const save = vi.spyOn(api, 'resolveReview').mockRejectedValue(new ApiError(409, { code: 'VERSION_CONFLICT', message: 'Тема изменилась.' }))
    render(<Harness />); await form(); submit()
    await screen.findByRole('button', { name: 'Использовать проверенные версии для нового решения' })
    expect(screen.getByLabelText('Причина решения Review')).toHaveProperty('value', 'Проверены источники')
    expect(save).toHaveBeenCalledTimes(1)
    expect(within(screen.getByRole('complementary', { name: 'Последние проверенные данные' })).getByText(/Проверенные версии затрагиваемых тем: synthetic-topic: 4/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', true)
    fireEvent.click(screen.getByRole('button', { name: 'Использовать проверенные версии для нового решения' }))
    expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', false)
    expect(screen.getByText(/Версии затрагиваемых тем: synthetic-topic: 4/)).toBeTruthy()
  })
  it('Review, закрытый другим пользователем, не считается нашим сохранением', async () => {
    const other = resolvedContext({ ...attachRequest, reason: 'Другая причина' }, 'f'.repeat(64))
    vi.spyOn(api, 'review').mockReset().mockResolvedValueOnce(reviewItem).mockResolvedValue(other.review)
    vi.spyOn(api, 'reviewHistory').mockReset().mockResolvedValueOnce([reviewItem]).mockResolvedValue(other.history)
    vi.spyOn(api, 'topicCatalog').mockResolvedValueOnce([reviewTarget]).mockResolvedValue(other.topics)
    const save = vi.spyOn(api, 'resolveReview').mockRejectedValue(new ApiError(409, { code: 'VERSION_CONFLICT', message: 'Review уже закрыт.' }))
    render(<Harness />); await form(); submit()
    await screen.findByText('Review уже закрыт.')
    expect(screen.queryByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')).toBeNull()
    expect(save).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('Автор решения Review')).toHaveProperty('value', attachRequest.author)
  })
  it.each([404, 422, 503])('при HTTP %s не теряет автора и причину', async status => {
    vi.spyOn(api, 'resolveReview').mockRejectedValue(new ApiError(status, { code: 'SERVER_ERROR', message: 'Сохранение не подтверждено.' }))
    render(<Harness />); await form(); submit()
    await screen.findByText('Сохранение не подтверждено.')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проверить Review без повторной записи' })).toHaveProperty('disabled', false))
    expect(screen.getByLabelText('Автор решения Review')).toHaveProperty('value', attachRequest.author)
    expect(screen.getByLabelText('Причина решения Review')).toHaveProperty('value', attachRequest.reason)
    if (status === 422) expect(screen.getByRole('button', { name: 'Использовать проверенные версии для нового решения' })).toBeTruthy()
    expect(screen.queryByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')).toBeNull()
  })
  it('requires_recheck блокирует запись и объясняет необходимость нового Review', async () => {
    vi.spyOn(api, 'review').mockReset().mockResolvedValue({ ...reviewItem, requires_recheck: true })
    const save = vi.spyOn(api, 'resolveReview')
    render(<Harness initial={{ ...reviewItem, requires_recheck: true }} />)
    await screen.findByText(/Его разрешение запрещено/)
    expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', true)
    expect(save).not.toHaveBeenCalled()
  })
  it('не показывает успех без прежней истории', async () => {
    const after = await prepareSaved()
    vi.spyOn(api, 'reviewHistory').mockReset().mockResolvedValueOnce([reviewItem]).mockResolvedValue([after.review])
    vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<Harness />); await form(); submit()
    await screen.findByText(/Прежняя история Review не подтверждена/)
    expect(screen.queryByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')).toBeNull()
  })
  it('ошибка первоначальной подготовки не уничтожает очередь и не разрешает запись', async () => {
    vi.spyOn(api, 'review').mockReset().mockRejectedValue(new ApiError(404, { code: 'REVIEW_NOT_FOUND', message: 'Review не найден.' }))
    render(<Harness />)
    await screen.findByText('Review не найден.')
    expect(screen.queryByRole('button', { name: 'Подтвердить решение Review' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Проверить Review без повторной записи' })).toBeTruthy()
  })
  it('отменяет незавершённое чтение при закрытии, поздний ответ не применяется', async () => {
    let finish: (value: ReviewItem) => void = () => {}
    const read = vi.spyOn(api, 'review').mockReset().mockReturnValue(new Promise(resolve => { finish = resolve }))
    const view = render(<Harness />)
    const signal = read.mock.calls[0][1]
    view.unmount(); expect(signal?.aborted).toBe(true)
    await act(async () => { finish(reviewItem) })
    expect(screen.queryByRole('form')).toBeNull()
  })
  it('обновляет очередь только после подтверждающего GET и истории', async () => {
    const after = await prepareSaved()
    let finish: (value: ReviewItem) => void = () => {}
    vi.spyOn(api, 'review').mockReset().mockResolvedValueOnce(reviewItem).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    vi.spyOn(api, 'reviews').mockResolvedValue([reviewItem])
    vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<ReviewPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    await screen.findByRole('article')
    fireEvent.click(screen.getByRole('button', { name: 'Решение и история Review' }))
    await form(); submit()
    await screen.findByText('Проверяем Review, историю и связи повторным GET…')
    expect(screen.getByRole('heading', { name: 'Назначение потребности · Ожидает проверки' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Загрузить очередь проверки' })).toHaveProperty('disabled', true)
    await act(async () => { finish(after.review) })
    await screen.findByRole('heading', { name: 'Назначение потребности · Разрешено' })
    expect(within(screen.getByRole('article')).getByText(/Решение Backend: attach/)).toBeTruthy()
  })
  it('выбор действия и подтверждение работают с клавиатуры', async () => {
    const user = userEvent.setup()
    const request = { version: 1, action: 'insufficient_data' as const, author: attachRequest.author, reason: attachRequest.reason, topic_versions: {} }
    const after = await prepareSaved(request)
    vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<Harness />); await form()
    screen.getByRole('button', { name: 'Недостаточно данных' }).focus()
    await user.keyboard(' ')
    screen.getByRole('button', { name: 'Подтвердить решение Review' }).focus()
    await user.keyboard('{Enter}')
    await screen.findByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')
  })

  it('после POST не утверждает отсутствие записи при гонке GET Review и истории', async () => {
    const after = await prepareSaved()
    vi.spyOn(api, 'review').mockReset().mockResolvedValue(reviewItem)
    const save = vi.spyOn(api, 'resolveReview').mockResolvedValue(after.review)
    render(<Harness />); await form(); submit()
    await screen.findByText('Review изменился во время чтения истории. Подтверждение результата не получено; повторите только чтение без новой записи.')
    expect(screen.queryByText(/запись не отправлена/)).toBeNull()
    expect(screen.queryByText('Решение Review подтверждено повторным GET, историей и проверкой связей.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Повторить ту же команду' })).toBeNull()
    expect(save).toHaveBeenCalledTimes(1)
  })
})
