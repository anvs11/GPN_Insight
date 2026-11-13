import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SignalMap } from './SignalMap'
import { workspaceCard } from '../test-fixtures/workspace'

const topics = Array.from({ length: 8 }, (_, i) => ({ ...workspaceCard, id: 'topic-' + i,
  title: 'Тема ' + (i + 1), source_ids: ['shared', 'source-' + i] }))

describe('Карта всех тем текущей страницы', () => {
  it('показывает восемь тем, все связи и не дублирует общий источник', () => {
    const view = render(<SignalMap cards={topics} onOpen={() => {}} />)
    expect(screen.getAllByRole('button', { name: /^Тема на карте:/ })).toHaveLength(8)
    expect(screen.getByText('Тем: 8 · связанных обращений: 9')).toBeTruthy()
    expect(view.container.querySelectorAll('.signal-edge')).toHaveLength(16)
    expect(view.container.querySelectorAll('.source-node')).toHaveLength(9)
    fireEvent.focus(screen.getByRole('button', { name: 'Тема на карте: Тема 8' }))
    expect(view.container.querySelectorAll('.signal-edge.highlighted')).toHaveLength(2)
    expect(view.container.querySelectorAll('.signal-edge.dimmed')).toHaveLength(14)
  })

  it('открывает позднюю тему с клавиатуры и учитывает новый набор фильтров', () => {
    const open = vi.fn()
    const view = render(<SignalMap cards={topics} onOpen={open} />)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Тема на карте: Тема 8' }), { key: 'Enter' })
    expect(open).toHaveBeenCalledWith(topics[7])
    view.rerender(<SignalMap cards={topics.slice(0, 1)} onOpen={open} />)
    expect(screen.getAllByRole('button', { name: /^Тема на карте:/ })).toHaveLength(1)
    expect(screen.getByText('Тем: 1 · связанных обращений: 2')).toBeTruthy()
    expect(view.container.querySelectorAll('.signal-edge')).toHaveLength(2)
  })
})
