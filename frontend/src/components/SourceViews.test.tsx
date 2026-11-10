import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { WorkspaceCard, WorkspacePage } from '../workspace-contracts'
import { workspaceCard } from '../test-fixtures/workspace'
import { sourceDetail } from '../test-fixtures/detail'
import { priorityLabels } from '../processing-contracts'
import { Monitoring } from './Monitoring'
import { SourceTable } from './SourceViews'

const source: WorkspaceCard = {
  ...workspaceCard, kind: 'source', id: sourceDetail.ticket.source_id, title: 'Синтетическое обращение',
  priorities: ['normal', 'unknown'], status: 'complete', material_count: 2,
}
const page = (items: WorkspaceCard[] = [source], total = items.length, offset = 0): WorkspacePage =>
  ({ items, total, offset, limit: 24, model_mode: 'replay' })
function setup() {
  return vi.spyOn(api, 'monitoring').mockImplementation(async filters =>
    page(filters.view === 'topics' ? [workspaceCard] : !filters.priority || source.priorities.includes(filters.priority as 'normal') ? [source] : []))
}
function Harness() {
  const [selected, setSelected] = useState<WorkspaceCard[]>([])
  return <Monitoring selected={selected} onToggle={card => setSelected(old =>
    old.some(item => item.id === card.id) ? old.filter(item => item.id !== card.id) : [...old, card])}
    onReports={() => {}} refresh={0} />
}
async function sources() {
  fireEvent.click(screen.getByRole('tab', { name: 'Обращения' }))
  const all = await screen.findByRole('region', { name: 'Все обращения' })
  await within(all).findByRole('button', { name: source.title })
  return all
}

describe('Колонки и список обращений', () => {
  it('запрашивает четыре серверных приоритета и сохраняет полный реестр без оценки', async () => {
    const load = setup()
    const unassessed = { ...source, id: 'synthetic-unassessed', title: 'Ещё без оценки', priorities: [], unassessed: true }
    load.mockImplementation(async filters => page(filters.view === 'topics' ? [workspaceCard]
      : filters.priority ? source.priorities.includes(filters.priority as 'normal') ? [source] : [] : [source, unassessed], filters.priority ? 1 : 480))
    render(<Harness />)
    const all = await sources()
    expect(within(all).getByText('480')).toBeTruthy()
    expect(within(all).getByRole('button', { name: unassessed.title })).toBeTruthy()
    const unknown = screen.getByRole('region', { name: priorityLabels.unknown })
    await within(unknown).findByRole('button', { name: source.title })
    expect(within(unknown).queryByRole('button', { name: unassessed.title })).toBeNull()
    for (const priority of ['normal', 'elevated', 'potentially_critical', 'unknown']) {
      expect(load).toHaveBeenCalledWith(expect.objectContaining({ view: 'sources', priority, limit: '24', offset: '0' }), expect.any(AbortSignal))
    }
    expect(screen.getByText(/счётчики не суммируются/)).toBeTruthy()
    for (const [args] of load.mock.calls) {
      expect(Object.values(args)).not.toContain('')
      expect(args).not.toHaveProperty('date_from')
      expect(args).not.toHaveProperty('category')
    }
  })

  it('листит каждую колонку отдельно и блокирует повторный запрос в процессе загрузки', async () => {
    const load = setup()
    let finish: (value: WorkspacePage) => void = () => {}
    load.mockImplementation(filters => filters.priority === 'normal' && filters.offset === '24'
      ? new Promise(resolve => { finish = resolve })
      : Promise.resolve(page(filters.view === 'topics' ? [workspaceCard] : [source], 480)))
    render(<Harness />)
    const all = await sources()
    const normal = screen.getByRole('region', { name: priorityLabels.normal })
    const next = await within(normal).findByRole('button', { name: 'Далее' })
    fireEvent.click(next); fireEvent.click(next)
    expect(next).toHaveProperty('disabled', true)
    expect(load.mock.calls.filter(([args]) => args.priority === 'normal' && args.offset === '24')).toHaveLength(1)
    await act(async () => finish(page([{ ...source, title: 'Следующая страница' }], 480, 24)))
    await within(normal).findByRole('button', { name: 'Следующая страница' })
    expect(within(all).getByText('1–24 из 480')).toBeTruthy()
    expect(within(normal).getByText('25–48 из 480')).toBeTruthy()
    fireEvent.click(within(normal).getByRole('button', { name: 'Назад' }))
    await within(normal).findByRole('button', { name: source.title })
  })

  it('ошибка одной колонки сохраняет прежние карточки и не мешает остальным', async () => {
    const load = setup()
    let failed = false
    load.mockImplementation(async filters => {
      if (filters.priority === 'normal' && filters.offset === '24' && !failed) {
        failed = true
        throw new ApiError(503, { code: 'TEMPORARY_UNAVAILABLE', message: 'Синтетическая недоступность' })
      }
      return page(filters.view === 'topics' ? [workspaceCard] : [source], 48, Number(filters.offset))
    })
    render(<Harness />)
    const all = await sources()
    const normal = screen.getByRole('region', { name: priorityLabels.normal })
    fireEvent.click(await within(normal).findByRole('button', { name: 'Далее' }))
    await within(normal).findByRole('alert')
    expect(within(normal).getByRole('checkbox')).toHaveProperty('disabled', true)
    expect(within(all).getByRole('checkbox')).toHaveProperty('disabled', false)
    expect(within(normal).getByText(/актуальность не подтверждена/)).toBeTruthy()
    fireEvent.click(within(normal).getByRole('button', { name: 'Обновить' }))
    await within(normal).findByText('25–48 из 48')
    expect(within(normal).queryByRole('alert')).toBeNull()
  })

  it('показывает пустую колонку без выдуманных карточек', async () => {
    setup(); render(<Harness />); await sources()
    const critical = screen.getByRole('region', { name: priorityLabels.potentially_critical })
    await within(critical).findByText('По этим условиям обращений нет.')
    expect(within(critical).queryByRole('article')).toBeNull()
    expect(within(critical).getByText('0')).toBeTruthy()
  })

  it('выбор одной карточки синхронизируется между колонками и списком', async () => {
    setup(); render(<Harness />)
    const all = await sources()
    fireEvent.click(within(all).getByRole('checkbox'))
    const normal = screen.getByRole('region', { name: priorityLabels.normal })
    await waitFor(() => expect(within(normal).getByRole('checkbox')).toHaveProperty('checked', true))
    fireEvent.click(screen.getByRole('button', { name: 'Список' }))
    expect(within(screen.getByRole('table')).getByRole('checkbox')).toHaveProperty('checked', true)
    fireEvent.click(within(screen.getByRole('table')).getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Колонки по приоритету' }))
    expect(within(screen.getByRole('region', { name: 'Все обращения' })).getByRole('checkbox')).toHaveProperty('checked', false)
  })

  it('изменяет фильтры только по Применить и передаёт поиск, категорию, статус и даты', async () => {
    const load = setup()
    render(<Harness />); await sources()
    const calls = load.mock.calls.length
    fireEvent.change(screen.getByLabelText('Поиск по мониторингу'), { target: { value: 'синтетическое' } })
    fireEvent.change(screen.getByLabelText('Категория'), { target: { value: 'operational_request' } })
    fireEvent.change(screen.getByLabelText('Обработка'), { target: { value: 'partial' } })
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-11-02' } })
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-11-08' } })
    expect(load).toHaveBeenCalledTimes(calls)
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }))
    await waitFor(() => expect(load).toHaveBeenCalledWith(expect.objectContaining({
      view: 'sources', priority: 'unknown', q: 'синтетическое', category: 'operational_request',
      status: 'partial', date_from: '2026-11-02', date_to: '2026-11-08', offset: '0',
    }), expect.any(AbortSignal)))
    fireEvent.change(screen.getByLabelText('Приоритет'), { target: { value: 'normal' } })
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: priorityLabels.unknown })).toBeNull())
    await waitFor(() => expect(screen.getByRole('region', { name: priorityLabels.normal }).getAttribute('aria-busy')).toBe('false'))
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }))
    await screen.findByRole('region', { name: 'Все обращения' })
    expect(screen.getByLabelText('Поиск по мониторингу')).toHaveProperty('value', '')
  })

  it('не отправляет запрос при перевёрнутом периоде', async () => {
    const load = setup(); render(<Harness />); await sources()
    const calls = load.mock.calls.length
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-11-08' } })
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-11-02' } })
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }))
    expect(screen.getByRole('alert').textContent).toContain('Начало периода позже окончания')
    expect(load).toHaveBeenCalledTimes(calls)
  })

  it('отменяет колонки при переключении в список и игнорирует поздние ответы', async () => {
    const load = setup()
    let resolve: (value: WorkspacePage) => void = () => {}
    let signal: AbortSignal | undefined
    load.mockImplementation((filters, currentSignal) => {
      if (filters.priority === 'normal') {
        signal = currentSignal
        return new Promise(done => { resolve = done })
      }
      return Promise.resolve(page(filters.view === 'topics' ? [workspaceCard] : [source]))
    })
    render(<Harness />); await sources()
    fireEvent.click(screen.getByRole('button', { name: 'Список' }))
    expect(signal?.aborted).toBe(true)
    await act(async () => resolve(page([{ ...source, title: 'Устаревший ответ' }])))
    expect(screen.queryByText('Устаревший ответ')).toBeNull()
    expect(screen.getByRole('table')).toBeTruthy()
  })

  it('старый ответ колонки не попадает в новый фильтр', async () => {
    const load = setup()
    let resolve: (value: WorkspacePage) => void = () => {}
    let signal: AbortSignal | undefined
    load.mockImplementation((filters, currentSignal) => {
      if (filters.priority === 'normal' && !filters.q) {
        signal = currentSignal
        return new Promise(done => { resolve = done })
      }
      return Promise.resolve(page(filters.view === 'topics' ? [workspaceCard] : [source]))
    })
    render(<Harness />); await sources()
    fireEvent.change(screen.getByLabelText('Поиск по мониторингу'), { target: { value: 'новый фильтр' } })
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }))
    await waitFor(() => expect(signal?.aborted).toBe(true))
    await act(async () => resolve(page([{ ...source, title: 'Старый фильтр' }])))
    expect(screen.queryByRole('button', { name: 'Старый фильтр' })).toBeNull()
  })

  it('ошибка 422 в списке сохраняет фильтр и последние данные, запрещая выбор устаревших', async () => {
    const load = setup()
    render(<Harness />); await sources()
    fireEvent.click(screen.getByRole('button', { name: 'Список' }))
    load.mockRejectedValue(new ApiError(422, { code: 'MONITORING_FILTER_INVALID', message: 'Проверьте фильтр.' }))
    fireEvent.change(screen.getByLabelText('Поиск по мониторингу'), { target: { value: 'сохранить запрос' } })
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }))
    await screen.findByText(/Проверьте фильтр/)
    expect(screen.getByLabelText('Поиск по мониторингу')).toHaveProperty('value', 'сохранить запрос')
    expect(within(screen.getByRole('table')).getByRole('checkbox')).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: source.title })).toBeTruthy()
  })

  it('открытый источник остаётся открытым при смене вида, фильтров и тем', async () => {
    setup(); vi.spyOn(api, 'source').mockResolvedValue(sourceDetail)
    render(<Harness />)
    const all = await sources()
    fireEvent.click(within(all).getByRole('button', { name: source.title }))
    await screen.findByRole('dialog', { name: 'Исходное обращение' })
    fireEvent.click(screen.getByRole('button', { name: 'Список' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Темы' }))
    expect(screen.getByRole('dialog', { name: 'Исходное обращение' })).toBeTruthy()
  })

  it('список показывает реальные поля и позволяет выбирать Space и открывать Enter', async () => {
    const user = userEvent.setup()
    const select = vi.fn(), open = vi.fn()
    render(<SourceTable page={page()} selected={[]} loading={false} error="" onSelect={select} onOpen={open} />)
    const checkbox = screen.getByRole('checkbox')
    checkbox.focus(); await user.keyboard(' ')
    expect(select).toHaveBeenCalledExactlyOnceWith(source)
    expect(open).not.toHaveBeenCalled()
    const button = screen.getByRole('button', { name: source.title })
    button.focus(); await user.keyboard('{Enter}')
    expect(open).toHaveBeenCalledExactlyOnceWith(source)
    expect(screen.getByText('Синтетические данные', { exact: false })).toBeTruthy()
    expect(screen.getByText('2')).toBeTruthy()
  })

  it('отображает все приоритеты, отсутствие данных и предупреждения в списке', () => {
    render(<SourceTable page={page([{ ...source, errors: 1, pending: true },
      { ...source, id: 'unassessed', title: 'Без оценки', priorities: [], unassessed: true },
      { ...source, id: 'empty', title: 'Без потребностей', priorities: [], unassessed: false }])}
      selected={[]} loading={false} error="" onSelect={() => {}} onOpen={() => {}} />)
    expect(screen.getByText(/Ошибок материалов: 1/)).toBeTruthy()
    expect(screen.getByText('Есть потребности без оценки')).toBeTruthy()
    expect(screen.getByText('Нет данных')).toBeTruthy()
    expect(screen.getByText(priorityLabels.unknown)).toBeTruthy()
    expect(screen.getByText(priorityLabels.normal)).toBeTruthy()
  })

  it('подключает списочную пагинацию к серверу и не загружает 480 карточек сразу', async () => {
    const load = setup()
    load.mockImplementation(async filters => page(filters.view === 'topics' ? [workspaceCard] : [source], 480, Number(filters.offset)))
    render(<Harness />); await sources()
    fireEvent.click(screen.getByRole('button', { name: 'Список' }))
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }))
    await screen.findByText('25–48 из 480')
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ view: 'sources', offset: '24', limit: '24' }), expect.any(AbortSignal))
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2)
  })

  it('темы сохраняют прежние карточки и не загружают колонки обращений', async () => {
    const load = setup()
    render(<Harness />)
    await screen.findByRole('button', { name: workspaceCard.title })
    expect(screen.queryByRole('group', { name: 'Вид обращений' })).toBeNull()
    expect(load).toHaveBeenCalledTimes(1)
  })
})
