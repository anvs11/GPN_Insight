import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import { priorityLabels, categoryLabels } from '../processing-contracts'
import type { Priority } from '../processing-contracts'
import type { WorkspaceCard as Card, WorkspacePage } from '../workspace-contracts'
import { cardKey, cardStatus } from '../workspace-labels'
import { formatTopicDate } from '../topic-labels'

interface CardActions {
  selected: Card[]
  onSelect: (card: Card) => void
  onOpen: (card: Card) => void
}
interface PageState {
  page: WorkspacePage | null
  loading: boolean
  error: string
  onOffset: (offset: number) => void
}
const priorities: Priority[] = ['potentially_critical', 'elevated', 'normal', 'unknown']

export function SourcePagination({ page, loading, onOffset }: PageState) {
  if (!page || page.total <= page.limit) return null
  return <div className="pagination">
    <button disabled={loading || page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>Назад</button>
    <span>{page.offset + 1}–{Math.min(page.offset + page.limit, page.total)} из {page.total}</span>
    <button disabled={loading || page.offset + page.limit >= page.total} onClick={() => onOffset(page.offset + page.limit)}>Далее</button>
  </div>
}

function Column({ title, priority, page, loading, error, onOffset, onRetry, children }: PageState & {
  title: string; priority?: Priority; onRetry: () => void; children: ReactNode
}) {
  const scroll = useRef<HTMLDivElement>(null)
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = 0 }, [page?.offset])
  return <section className="source-column surface" aria-label={title} aria-busy={loading}>
    <header className="source-column-heading">
      <h3><span className={priority ? 'priority-badge priority-' + priority : ''}>{title}</span>
        <span className="count-chip">{page?.total ?? '—'}</span></h3>
      <button className="text-button" disabled={loading} onClick={onRetry}>Обновить</button>
    </header>
    {loading && <p role="status" className="mode-notice">Загружаем обращения…</p>}
    {error && <p role="alert" className="workspace-error">{error} {page && 'Прежние данные сохранены, их актуальность не подтверждена.'}</p>}
    <div ref={scroll} className={'source-column-scroll' + (loading || error ? ' is-stale' : '')}
      tabIndex={0} role="region" aria-label={title + ' — карточки'}>
      {!loading && !error && page?.items.length === 0 && <p className="source-column-empty">По этим условиям обращений нет.</p>}
      {children}
    </div>
    <SourcePagination page={page} loading={loading} error={error} onOffset={onOffset} />
  </section>
}

function ColumnCards({ page, loading, error, selected, onSelect, onOpen }: CardActions & Pick<PageState, 'page' | 'loading' | 'error'>) {
  return page?.items.map(card => <article key={cardKey(card)} className="source-compact-card" onClick={event => {
    if (event.target instanceof Element && !event.target.closest('button,input,label')) onOpen(card)
  }}>
    <input type="checkbox" checked={selected.some(item => cardKey(item) === cardKey(card))}
      disabled={loading || Boolean(error)} onChange={() => onSelect(card)} aria-label={'В отчёт: ' + card.title} />
    <div className="source-compact-content">
      <button className="card-title" title={card.title} onClick={() => onOpen(card)}>{card.title}</button>
      <small title={[card.id, cardStatus(card), ...card.priorities.map(value => priorityLabels[value])].join(' · ')}>
        {card.synthetic && <span>Синтетические данные · </span>}{cardStatus(card)}
      </small>
    </div>
    {(card.errors > 0 || card.pending) && <span className="card-warning" role="img"
      aria-label={card.errors > 0 ? 'Есть ошибки материалов' : 'Нужна повторная проверка'}
      title={card.errors > 0 ? 'Ошибок материалов: ' + card.errors : 'Нужна повторная проверка'}>⚠</span>}
  </article>)
}

function PriorityColumn({ priority, filters, ...actions }: CardActions & { priority: Priority; filters: Record<string, string> }) {
  const [page, setPage] = useState<WorkspacePage | null>(null)
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    async function load() {
      try {
        const args: Record<string, string> = { view: 'sources', priority, offset: String(offset), limit: '24' }
        for (const [key, value] of Object.entries(filters)) if (value) args[key] = value
        const result = await api.monitoring(args, controller.signal)
        if (!controller.signal.aborted) { setPage(result); setError('') }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Колонка недоступна. Повторите чтение.')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [filters, priority, offset, revision])
  function navigate(next: number) {
    request.current?.abort()
    setLoading(true)
    setError('')
    setOffset(next)
    setRevision(value => value + 1)
  }
  return <Column title={priorityLabels[priority]} priority={priority} page={page} loading={loading} error={error}
    onOffset={navigate} onRetry={() => navigate(offset)}>
    <ColumnCards {...actions} page={page} loading={loading} error={error} />
  </Column>
}

export function SourceColumns({ filters, onRetry, ...props }: CardActions & PageState & {
  filters: Record<string, string>; onRetry: () => void
}) {
  const priority = filters.priority as Priority | ''
  return <>
    <p className="mode-notice source-columns-note">В каждой колонке своя прокрутка и страницы по 24 обращения.
      {!priority && ' Обращение с несколькими приоритетами видно в каждой подходящей колонке, поэтому счётчики не суммируются. Без оценки — в «Все обращения».'}</p>
    <div className="source-board" tabIndex={0} role="region" aria-label="Колонки обращений по приоритету">
      <Column title={priority ? priorityLabels[priority] : 'Все обращения'} priority={priority || undefined}
        {...props} onRetry={onRetry}>
        <ColumnCards {...props} />
      </Column>
      {!priority && priorities.map(value => <PriorityColumn key={value} priority={value} filters={filters}
        selected={props.selected} onSelect={props.onSelect} onOpen={props.onOpen} />)}
    </div>
  </>
}

export function SourceTable({ page, loading, error, selected, onSelect, onOpen }: CardActions & Pick<PageState, 'page' | 'loading' | 'error'>) {
  return <div className={'source-table-scroll surface' + (loading || error ? ' is-stale' : '')}
    tabIndex={0} role="region" aria-label="Список обращений">
    <table className="source-table">
      <caption className="sr-only">Обращения по выбранным фильтрам. Сначала последние.</caption>
      <thead><tr><th scope="col">В отчёт</th><th scope="col">Обращение</th><th scope="col">Приоритет</th>
        <th scope="col">Категория</th><th scope="col">Обработка</th><th scope="col">Создано (МСК)</th><th scope="col">Вложений</th></tr></thead>
      <tbody>{page?.items.map(card => <tr key={cardKey(card)} onClick={event => {
        if (event.target instanceof Element && !event.target.closest('button,input,label,a')) onOpen(card)
      }}>
        <td><input type="checkbox" checked={selected.some(item => cardKey(item) === cardKey(card))}
          disabled={loading || Boolean(error)} onChange={() => onSelect(card)} aria-label={'В отчёт: ' + card.title} /></td>
        <th scope="row"><button className="card-title" onClick={() => onOpen(card)}>{card.title}</button>
          <small>{card.id} · {card.synthetic ? 'Синтетические данные · ' : ''}{card.origins.includes('authored') ? 'Подготовлено вручную' : card.origins.includes('model') ? 'Ответ модели' : 'Импортировано'}</small>
          {(card.errors > 0 || card.pending) && <small className="card-warning">{card.errors > 0 ? 'Ошибок материалов: ' + card.errors + '. ' : ''}{card.pending ? 'Нужна повторная проверка.' : 'Покрытие неполное.'}</small>}</th>
        <td>{card.priorities.map(value => <span key={value} className={'priority-badge priority-' + value}>{priorityLabels[value]}</span>)}
          {card.unassessed && <span className="priority-badge priority-unknown">Есть потребности без оценки</span>}
          {!card.priorities.length && !card.unassessed && 'Нет данных'}</td>
        <td>{card.categories.map(value => categoryLabels[value]).join(', ') || 'Категория не определена'}</td>
        <td><span className={'status-pill status-' + card.status}>{cardStatus(card)}</span></td>
        <td>{formatTopicDate(card.created_at)}</td><td>{card.material_count}</td>
      </tr>)}</tbody>
    </table>
  </div>
}
