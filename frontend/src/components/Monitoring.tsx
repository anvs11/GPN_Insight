import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { WorkspaceCard as Card, WorkspacePage } from '../workspace-contracts'
import { categoryLabels, priorityLabels } from '../processing-contracts'
import { topicStatusLabels } from '../topic-labels'
import { WorkspaceCard } from './WorkspaceCard'
import { cardKey, sourceStateLabels } from '../workspace-labels'
import { TopicDetail } from './TopicDetail'
import { SourceViewer } from './SourceViewer'
import { WorkspaceDrawer } from './WorkspaceDrawer'
import { SignalMap } from './SignalMap'
import { SourceColumns, SourceTable, SourcePagination } from './SourceViews'

const defaults = { q: '', category: '', priority: '', status: '', support: 'all', date_from: '', date_to: '' }
export function Monitoring({ selected, onToggle, onReports, refresh }: {
  selected: Card[]; onToggle: (card: Card) => void; onReports: () => void; refresh: number
}) {
  const [sourceLayout, setSourceLayout] = useState<'columns' | 'list'>('columns')
  const [view, setView] = useState<'topics' | 'sources'>('topics')
  const [draft, setDraft] = useState(defaults), [filters, setFilters] = useState(defaults)
  const [offset, setOffset] = useState(0), [revision, setRevision] = useState(0)
  const [page, setPage] = useState<WorkspacePage | null>(null), [loading, setLoading] = useState(true)
  const [receivedView, setReceivedView] = useState(view)
  const [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [opened, setOpened] = useState<Card | null>(null)
  const busy = useRef(false), request = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController(); request.current = controller
    async function load() {
      setLoading(true)
      try {
        const args: Record<string, string> = { view, offset: String(offset), limit: '24' }
        for (const [key, value] of Object.entries(filters)) if (value) args[key] = value
        const result = await api.monitoring(args, controller.signal)
        if (!controller.signal.aborted) { setPage(result); setReceivedView(view); setError('') }
      } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Мониторинг недоступен. Повторите чтение.') }
      finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => controller.abort()
  }, [view, filters, offset, revision, refresh])
  function reload() { request.current?.abort(); setLoading(true); setError(''); setRevision(n => n + 1) }
  function changeView(next: typeof view) {
    if (next === view) return
    request.current?.abort(); setLoading(true); setError(''); setView(next); setOffset(0)
    setDraft(old => ({ ...old, status: '' })); setFilters(old => ({ ...old, status: '' }))
  }
  function close() { if (busy.current) { setNotice('Дождитесь ответа на сохранение. Панель остаётся открытой.'); return }; setOpened(null); setNotice('') }
  const currentPage = receivedView === view ? page : null
  const rows = currentPage?.items ?? []
  function openCard(card: Card) { busy.current = false; setOpened(card) }
  function navigate(next: number) { request.current?.abort(); setLoading(true); setError(''); setOffset(next) }
  return <>
    <div className="page-heading"><div><span className="eyebrow">ВЕБНЕФТЬ / ПРОДУКТОВАЯ ОБРАТНАЯ СВЯЗЬ</span><h1>Мониторинг <span className="count-chip">{currentPage?.total ?? '—'}</span></h1>
      <p>От обращений — к проверяемым потребностям и решениям.</p></div><button className="button-secondary" onClick={reload} disabled={loading}>↻ Обновить</button></div>
    <div className="monitor-tabs" role="tablist" aria-label="Представление мониторинга">
      <button role="tab" aria-selected={view === 'topics'} onClick={() => changeView('topics')}>Темы</button>
      <button role="tab" aria-selected={view === 'sources'} onClick={() => changeView('sources')}>Обращения</button>
    </div>
    {view === 'sources' && <div className="source-view-switch" role="group" aria-label="Вид обращений">
      <button className="button-secondary" aria-pressed={sourceLayout === 'columns'} onClick={() => setSourceLayout('columns')}>Колонки по приоритету</button>
      <button className="button-secondary" aria-pressed={sourceLayout === 'list'} onClick={() => setSourceLayout('list')}>Список</button>
    </div>}
    <p className="view-explanation">{view === 'topics' ? 'Темы объединяют похожие потребности из обращений. Повторяемость можно выбрать в фильтре ниже.' : 'Здесь все исходные обращения, включая неразобранные и ещё не обработанные.'}</p>
    <form className="monitor-filters surface" aria-label="Фильтры мониторинга" onSubmit={event => {
      event.preventDefault()
      if (draft.date_from && draft.date_to && draft.date_from > draft.date_to) { setNotice('Начало периода позже окончания. Исправьте даты.'); return }
      request.current?.abort(); setLoading(true); setError(''); setNotice(''); setFilters({ ...draft }); setOffset(0)
    }}>
      <div className="search-row"><span aria-hidden="true">⌕</span><input aria-label="Поиск по мониторингу" placeholder="Поиск по названию, содержанию и номеру" value={draft.q} maxLength={120} onChange={e => setDraft({ ...draft, q: e.target.value })} /><button className="button-primary">Найти</button></div>
      <div className="filter-grid">
        <label>Категория<select value={draft.category} onChange={e => setDraft({ ...draft, category: e.target.value })}><option value="">Все категории</option>{Object.entries(categoryLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
        <label>Приоритет<select value={draft.priority} onChange={e => setDraft({ ...draft, priority: e.target.value })}><option value="">Все приоритеты</option>{Object.entries(priorityLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
        <label>{view === 'topics' ? 'Статус темы' : 'Обработка'}<select value={draft.status} onChange={e => setDraft({ ...draft, status: e.target.value })}><option value="">Все статусы</option>{Object.entries(view === 'topics' ? topicStatusLabels : sourceStateLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
        <label>С даты<input type="date" value={draft.date_from} onChange={e => setDraft({ ...draft, date_from: e.target.value })} /></label>
        <label>По дату<input type="date" value={draft.date_to} onChange={e => setDraft({ ...draft, date_to: e.target.value })} /></label>
      </div>
      <div className="filter-footer"><span>{view === 'topics' ? 'Даты первого обращения в теме' : 'Исходные даты обращений'} · Москва</span>
        {view === 'topics' && <label className="inline-label">Повторяемость<select value={draft.support} onChange={e => setDraft({ ...draft, support: e.target.value })}><option value="all">Все темы</option><option value="repeated">Повторяющиеся</option><option value="single">Одиночные</option></select></label>}
        <button className="text-button" type="button" onClick={() => { request.current?.abort(); setDraft(defaults); setFilters(defaults); setOffset(0); reload() }}>Сбросить</button><button className="button-secondary">Применить</button></div>
    </form>
    {notice && <p role="alert" className="workspace-notice">{notice}</p>}
    {error && <p role="alert" className="workspace-error">{error} {page && 'Предыдущий результат сохранён; актуальность не подтверждена.'}</p>}
    <div className="monitor-columns"><section className="card-feed" aria-label={view === 'topics' ? 'Темы мониторинга' : 'Обращения мониторинга'}>
      <div className="feed-toolbar"><span>{loading ? 'Обновляем данные…' : currentPage ? `Найдено: ${currentPage.total}` : 'Результат не получен'}<small>Сначала последние обращения</small></span>
        <button className="button-primary report-navigation" onClick={onReports}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6" /></svg>В отчёт <span className="count-chip">{selected.length}</span></button></div>
      {page && <p className="mode-notice">{page.model_mode === 'replay' ? 'Replay: сохранённые ответы для известных входов. Качество живой модели не измеряется.' : 'Live: результаты требуют проверки по источникам.'}</p>}
      {loading && <p role="status" className="loading-line">Загружаем {view === 'topics' ? 'темы' : 'обращения'}…</p>}
      {!loading && !error && currentPage && !rows.length && <div className="empty-state"><span>⌕</span><h3>По этим условиям ничего не найдено</h3><p>Измените фильтры или загрузите обращения. Отсутствие карточек не доказывает отсутствие потребностей.</p></div>}
      {view === 'sources' ? sourceLayout === 'columns'
        ? <SourceColumns key={JSON.stringify([filters, revision, refresh])} filters={filters} page={currentPage} loading={loading} error={error}
            onOffset={navigate} onRetry={reload} selected={selected} onSelect={onToggle} onOpen={openCard} />
        : <><SourceTable page={currentPage} loading={loading} error={error} selected={selected} onSelect={onToggle} onOpen={openCard} />
            <SourcePagination page={currentPage} loading={loading} error={error} onOffset={navigate} /></>
        : <><div className={'insight-grid' + (loading || error ? ' is-stale' : '')}>{rows.map(card => <WorkspaceCard key={cardKey(card)} card={card} selected={selected.some(s => cardKey(s) === cardKey(card))}
        selectionDisabled={loading || Boolean(error)} onSelect={() => { if (!loading && !error) onToggle(card) }} onOpen={() => openCard(card)} />)}</div>
      <SourcePagination page={currentPage} loading={loading} error={error} onOffset={navigate} /></>}
    </section><aside className="monitor-aside">{view === 'topics' && <SignalMap cards={rows} onOpen={setOpened} />}
      <section className="surface aside-selection"><span className="eyebrow">ВАША ПОДБОРКА</span><h3>Соберите важное <span className="count-chip">{selected.length}</span></h3><p>Отметьте карточки, добавьте своё резюме и сохраните отдельный отчёт.</p><button className="button-primary" onClick={onReports}>Перейти к отчётам</button></section>
      <button className="button-secondary" onClick={() => {
        request.current?.abort(); setView('sources'); setDraft({ ...defaults, category: 'unclassified' }); setFilters({ ...defaults, category: 'unclassified' }); setOffset(0); setLoading(true)
      }}>Показать неразобранное</button>
      <section className="aside-note"><h4>Приоритет — с основанием</h4><p>«Потенциально критично» — сигнал для проверки. Обоснование, цитаты и неизвестности доступны в подробностях.</p></section>
    </aside></div>
    <WorkspaceDrawer open={opened !== null} title={opened?.kind === 'topic' ? 'Тема и решение' : 'Исходное обращение'} onClose={close} wide>
      {notice && opened && <p role="alert" className="workspace-notice">{notice}</p>}
      {opened?.kind === 'topic' && <TopicDetail key={opened.id} topicId={opened.id} onClose={close} onBusyChange={value => { busy.current = value }} onTopicUpdated={reload} />}
      {opened?.kind === 'source' && <SourceViewer key={opened.id} sourceId={opened.id} onClose={close} />}
    </WorkspaceDrawer>
  </>
}
