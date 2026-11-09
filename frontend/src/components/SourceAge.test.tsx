import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SourceAge } from './SourceAge'
import { formatSourceAge } from '../source-age'

afterEach(() => vi.useRealTimers())

describe('Возраст исходного обращения', () => {
  it('считает продолжительность независимо от часового пояса и перехода месяца', () => {
    const now = Date.parse('2026-11-02T09:01:00Z')
    expect(formatSourceAge('2026-10-31T11:00:00+03:00', now)).toBe('2 д. 1 ч. 1 мин.')
    expect(formatSourceAge('2026-11-02T09:00:30Z', now)).toBe('Меньше минуты')
    expect(formatSourceAge('2026-11-02T12:00:00+03:00', now)).toBe('1 мин.')
  })

  it('не выдаёт будущую или ошибочную дату за отрицательный срок', () => {
    const now = Date.parse('2026-11-02T09:01:00Z')
    expect(formatSourceAge('2026-11-03T09:00:00Z', now)).toMatch(/ещё не наступила/)
    expect(formatSourceAge('bad-date', now)).toBe('Не удалось определить возраст')
  })

  it('обновляет часы, принимает другую дату и освобождает таймер при закрытии', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-02T09:00:00Z'))
    const view = render(<dl><SourceAge createdAt="2026-11-02T08:00:00Z" /></dl>)
    expect(screen.getByText('1 ч. 0 мин.')).toBeTruthy()
    act(() => vi.advanceTimersByTime(60_000))
    expect(screen.getByText('1 ч. 1 мин.')).toBeTruthy()
    view.rerender(<dl><SourceAge createdAt="2026-11-02T09:00:00Z" /></dl>)
    expect(screen.getByText('1 мин.')).toBeTruthy()
    expect(screen.getByText(/Статус закрытия.*неизвестен/)).toBeTruthy()
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
