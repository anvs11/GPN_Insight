import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { ReviewItem } from '../api-types'
import { reviewItem } from '../test-fixtures/import'
import { sourceDetail } from '../test-fixtures/detail'
import { ReviewPanel } from './ReviewPanel'

describe('Чтение очереди проверки результатов', () => {
  it('не считает отсутствие очереди успехом анализа и не запрашивает её до нажатия', async () => {
    const read = vi.spyOn(api, 'reviews').mockResolvedValue([])
    render(<ReviewPanel />)
    expect(read).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    await screen.findByText(/Backend вернул пустую очередь/)
    expect(screen.getByText(/Отсутствие записей не доказывает успешность анализа/)).toBeTruthy()
  })
  it('показывает версию, основание и требует сверки текущего источника', async () => {
    vi.spyOn(api, 'reviews').mockResolvedValue([{ ...reviewItem, requires_recheck: true, evidence_refs: [{
      source_id: sourceDetail.ticket.source_id, fragment_id: sourceDetail.document!.fragments[0].fragment_id, quote: 'Проверяемая цитата',
    }] }])
    const source = vi.spyOn(api, 'source').mockResolvedValue(sourceDetail)
    render(<ReviewPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    await screen.findByRole('article', { name: 'Проверка synthetic-review' })
    expect(screen.getByText(/ID: synthetic-review · Версия: 1/)).toBeTruthy()
    expect(screen.getByText(/Требуется повторная проверка/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Проверить цитату/ }))
    await screen.findByText(/Синтетический демонстрационный источник/)
    expect(source).toHaveBeenCalledWith(sourceDetail.ticket.source_id, expect.any(AbortSignal))
  })
  it('сохраняет прежнюю очередь при ошибке, помечая её устаревшей', async () => {
    vi.spyOn(api, 'reviews').mockResolvedValueOnce([reviewItem]).mockRejectedValueOnce(new ApiError(422, {
      code: 'VALIDATION_ERROR', message: 'Фильтр отклонён.',
    }))
    render(<ReviewPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    await screen.findByRole('article')
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    await screen.findByText('Фильтр отклонён.')
    expect(screen.getByRole('article')).toBeTruthy()
    expect(screen.getByText(/Показана ранее полученная очередь/)).toBeTruthy()
  })
  it('не отображает старый ответ после обновления запроса', async () => {
    let finish: (value: ReviewItem[]) => void = () => {}
    const read = vi.spyOn(api, 'reviews').mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
      .mockResolvedValueOnce([])
    render(<ReviewPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    const signal = read.mock.calls[0][0]
    fireEvent.click(screen.getByRole('button', { name: 'Обновить запрос очереди' }))
    await screen.findByText(/Backend вернул пустую очередь/)
    expect(signal?.aborted).toBe(true)
    await act(async () => { finish([reviewItem]) })
    expect(screen.queryByRole('article')).toBeNull()
  })
  it('отменяет чтение при закрытии экрана', async () => {
    const read = vi.spyOn(api, 'reviews').mockReturnValue(new Promise(() => {}))
    const view = render(<ReviewPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить очередь проверки' }))
    view.unmount()
    expect(read.mock.calls[0][0]?.aborted).toBe(true)
  })
})
