import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { DecisionUpdate } from '../api-types'
import { savedDecision, topicDetail } from '../test-fixtures/detail'
import { TopicDetail } from './TopicDetail'

describe('Бизнес-решения темы с обязательными основаниями', () => {
  it.each([
    { status: 'linked' as const, fields: [['Номер или ссылка существующей задачи', 'DEV-42']], extras: { existing_issue_ref: 'DEV-42' } },
    { status: 'deferred' as const, fields: [['Дата пересмотра', '2026-11-20']], extras: { review_after: '2026-11-20' } },
    { status: 'implemented' as const, fields: [['Дата реализации (Москва)', '2026-11-10T12:00'], ['Описание результата', 'Внедрено исправление формы']], extras: { implemented_at: '2026-11-10T12:00:00+03:00', implementation_comment: 'Внедрено исправление формы' } },
  ])('подтверждает $status только с нужными полями, версией и сохранённой историей', async ({ status, fields, extras }) => {
    let current = topicDetail
    vi.spyOn(api, 'topic').mockImplementation(async () => current)
    const save = vi.spyOn(api, 'saveDecision').mockImplementation(async (_id, request) => {
      current = savedDecision(topicDetail, request)
      if (request.implemented_at) current.history.at(-1)!.implemented_at = new Date(request.implemented_at).toISOString()
      return current
    })
    render(<TopicDetail topicId={topicDetail.topic.topic_id} onClose={() => {}} />)
    await screen.findByRole('region', { name: 'Решение владельца продукта' })
    fireEvent.change(screen.getByLabelText('Статус решения'), { target: { value: status } })
    fireEvent.change(screen.getByLabelText('Причина решения'), { target: { value: '  Синтетическое основание  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    expect(save).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toBeTruthy()
    for (const [label, value] of fields) fireEvent.change(screen.getByLabelText(label), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }))
    await screen.findByText(/Сохранение подтверждено повторным чтением/)
    const expected: DecisionUpdate = { status, owner: topicDetail.topic.owner!, reason: 'Синтетическое основание', version: topicDetail.topic.version, ...extras }
    expect(save).toHaveBeenCalledWith(topicDetail.topic.topic_id, expected, expect.any(AbortSignal))
    expect(current.history.length).toBe(topicDetail.history.length + 1)
    expect(current.history[0]).toEqual(topicDetail.history[0])
  })
})
