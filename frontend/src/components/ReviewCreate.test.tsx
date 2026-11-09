/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useCallback, useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { ReviewItem } from '../api-types'
import type { ReviewContext } from '../review-verification'
import { reviewRequestHash } from '../review-verification'
import { createRequest, proposedTopic, resolvedContext, reviewContext, reviewSource } from '../test-fixtures/review'
import { ReviewDecisionForm } from './ReviewDecisionForm'

let current: ReviewContext
let readFails = false
function Harness() {
  const [item, setItem] = useState(reviewContext.review)
  const onRead = useCallback((value: ReviewItem) => setItem(value), [])
  const onBusyChange = useCallback(() => true, [])
  return <ReviewDecisionForm item={item} stale={false} onRead={onRead} onBusyChange={onBusyChange} />
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  current = structuredClone(reviewContext); readFails = false
  vi.spyOn(api, 'review').mockImplementation(async () => {
    if (readFails) throw new ApiError(503, { code: 'READ_FAILED', message: 'Чтение недоступно.' })
    return current.review
  })
  vi.spyOn(api, 'reviewHistory').mockImplementation(async () => current.history)
  vi.spyOn(api, 'topicCatalog').mockImplementation(async () => current.topics)
  vi.spyOn(api, 'source').mockResolvedValue(reviewSource)
  vi.spyOn(api, 'topicChanges').mockImplementation(async () => current.createdChanges ?? [])
})
async function fill() {
  await screen.findByLabelText('Автор решения Review')
  fireEvent.click(screen.getByRole('button', { name: 'Создать новую тему' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', false))
  for (const [label, value] of [
    ['Название новой темы', proposedTopic.title], ['Краткое описание новой темы', proposedTopic.summary],
    ['Сценарий пользователя новой темы', proposedTopic.scenario], ['Потребность новой темы', proposedTopic.need_statement],
    ['Границы включения и исключения новой темы', proposedTopic.scope], ['Текст примера включения 1', proposedTopic.inclusion_examples[0].text],
    ['Автор решения Review', createRequest.author], ['Причина решения Review', createRequest.reason],
  ]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.click(screen.getByRole('checkbox', { name: /Изменить контакт в заявке/ }))
}
function submit() { fireEvent.submit(screen.getByRole('form')) }
async function save() {
  current = resolvedContext(createRequest, await reviewRequestHash('synthetic-review', createRequest))
  return current.review
}
const success = 'Решение Review подтверждено повторным GET, историей и проверкой связей.'

describe('Создать новую тему в форме Review', () => {
  it('отправляет точный ProposedTopic, проверяет историю и каталог, не дублирует POST', async () => {
    const post = vi.spyOn(api, 'resolveReview').mockImplementation(save)
    render(<Harness />); await fill(); submit(); submit()
    await screen.findByText(success)
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][1]).toEqual(createRequest)
    expect(api.topicChanges).toHaveBeenCalledWith('synthetic-created-topic', expect.any(AbortSignal))
    expect(screen.getByText(/В списке выберите «Все темы»/)).toBeTruthy()
  })
  it('показывает обязательные поля без автоматического наполнения и не даёт подменять цитату', async () => {
    const post = vi.spyOn(api, 'resolveReview')
    render(<Harness />); await fill()
    fireEvent.change(screen.getByLabelText('Краткое описание новой темы'), { target: { value: ' ' } })
    submit(); await screen.findByText(/Заполните название/)
    expect(post).not.toHaveBeenCalled()
    expect(screen.queryByDisplayValue(reviewContext.review.evidence_refs[0].quote)).toBeNull()
  })
  it('сохраняет паспорт при переключении действий', async () => {
    render(<Harness />); await fill()
    fireEvent.click(screen.getByRole('button', { name: 'Недостаточно данных' }))
    fireEvent.click(screen.getByRole('button', { name: 'Создать новую тему' }))
    await waitFor(() => expect(screen.getByLabelText('Название новой темы')).toHaveProperty('value', proposedTopic.title))
    expect(screen.getByRole('checkbox', { name: /Изменить контакт в заявке/ })).toHaveProperty('checked', true)
  })
  it.each([409, 422, 404])('сохраняет черновик при HTTP %s и требует явного принятия проверенных версий', async status => {
    const post = vi.spyOn(api, 'resolveReview').mockRejectedValue(new ApiError(status, { code: 'REJECTED', message: 'Отклонено сервером.' }))
    render(<Harness />); await fill(); submit()
    await screen.findByText('Отклонено сервером.')
    await screen.findByRole('button', { name: 'Использовать проверенные версии для нового решения' })
    expect(screen.getByLabelText('Название новой темы')).toHaveProperty('value', proposedTopic.title)
    expect(screen.getByLabelText('Причина решения Review')).toHaveProperty('value', createRequest.reason)
    expect(screen.getByRole('checkbox', { name: /Изменить контакт в заявке/ })).toHaveProperty('checked', true)
    expect(screen.queryByText(success)).toBeNull(); expect(post).toHaveBeenCalledTimes(1)
  })
  it('не позволяет принять старые версии после 409, если новый GET не удался', async () => {
    const post = vi.spyOn(api, 'resolveReview').mockImplementation(async () => {
      readFails = true
      throw new ApiError(409, { code: 'VERSION_CONFLICT', message: 'Версия изменилась.' })
    })
    render(<Harness />); await fill(); submit()
    await screen.findByText('Чтение недоступно.')
    expect(screen.queryByRole('button', { name: 'Использовать проверенные версии для нового решения' })).toBeNull()
    expect(screen.getByLabelText('Название новой темы')).toHaveProperty('value', proposedTopic.title)
    readFails = false
    fireEvent.click(screen.getByRole('button', { name: 'Проверить Review без повторной записи' }))
    await screen.findByRole('button', { name: 'Использовать проверенные версии для нового решения' })
    expect(post).toHaveBeenCalledTimes(1)
  })
  it('после потерянного ответа подтверждает созданную тему без второго POST', async () => {
    const post = vi.spyOn(api, 'resolveReview').mockImplementation(async () => {
      await save(); throw new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Ответ потерян.' })
    })
    render(<Harness />); await fill(); submit()
    await screen.findByText(success)
    expect(post).toHaveBeenCalledTimes(1)
    expect(current.topics.filter(card => card.topic.topic_id === 'synthetic-created-topic')).toHaveLength(1)
  })
  it('после принятого POST и недоступного GET блокирует новую запись до повторного чтения', async () => {
    const post = vi.spyOn(api, 'resolveReview').mockImplementation(async () => { const result = await save(); readFails = true; return result })
    render(<Harness />); await fill(); submit()
    await screen.findByText('Чтение недоступно.')
    expect(screen.queryByText(success)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Повторить ту же команду' })).toBeNull()
    readFails = false
    fireEvent.click(screen.getByRole('button', { name: 'Проверить Review без повторной записи' }))
    await screen.findByText(success); expect(post).toHaveBeenCalledTimes(1)
  })
  it('не подтверждает успешный POST без новой темы или её истории', async () => {
    vi.spyOn(api, 'resolveReview').mockImplementation(async () => { const result = await save(); current.createdChanges = []; return result })
    render(<Harness />); await fill(); submit()
    await screen.findByText(/GET и история не подтвердили/)
    expect(screen.queryByText(success)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Повторить ту же команду' })).toBeNull()
  })
  it('после неудачи чтения источника восстанавливается только GET и не стирает введённое', async () => {
    vi.spyOn(api, 'source').mockRejectedValueOnce(new ApiError(503, { code: 'SOURCE_FAILED', message: 'Источник недоступен.' }))
    render(<Harness />); await screen.findByLabelText('Автор решения Review')
    fireEvent.click(screen.getByRole('button', { name: 'Создать новую тему' }))
    await screen.findByText('Источник недоступен.')
    fireEvent.change(screen.getByLabelText('Название новой темы'), { target: { value: 'Мой черновик' } })
    fireEvent.click(screen.getByRole('button', { name: 'Проверить Review без повторной записи' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Подтвердить решение Review' })).toHaveProperty('disabled', false))
    expect(screen.getByLabelText('Название новой темы')).toHaveProperty('value', 'Мой черновик')
  })
  it('создаёт первую тему из Review даже при пустом каталоге', async () => {
    current.topics = []
    const post = vi.spyOn(api, 'resolveReview').mockImplementation(async () => {
      current = resolvedContext(createRequest, await reviewRequestHash('synthetic-review', createRequest), current)
      return current.review
    })
    render(<Harness />); await fill(); submit()
    await screen.findByText(success)
    expect(post).toHaveBeenCalledTimes(1)
  })
  it('отменяет загрузку источника при размонтировании и не начинает POST', async () => {
    let signal: AbortSignal | undefined
    vi.spyOn(api, 'source').mockImplementation((_id, value) => { signal = value; return new Promise(() => {}) })
    const post = vi.spyOn(api, 'resolveReview')
    const ui = render(<Harness />); await screen.findByLabelText('Автор решения Review')
    fireEvent.click(screen.getByRole('button', { name: 'Создать новую тему' }))
    await waitFor(() => expect(signal).toBeTruthy())
    await act(async () => ui.unmount())
    expect(signal?.aborted).toBe(true); expect(post).not.toHaveBeenCalled()
  })
})
