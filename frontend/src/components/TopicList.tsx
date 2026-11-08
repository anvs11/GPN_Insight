import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { TopicFilters, TopicListItem, TopicStatus } from '../api-types'
import { topicStatusLabels } from '../topic-labels'
import { TopicDetail } from './TopicDetail'
import './TopicList.css'

type Filters = { status: TopicStatus | ''; support_level: 'single' | 'repeated' | 'all'; date_from: string; date_to: string }
const defaults: Filters = { status: '', support_level: 'repeated', date_from: '', date_to: '' }
const supportLabels = { repeated: 'Повторяющиеся', single: 'Одиночные', all: 'Все темы' }

function filterSummary(filters: Filters) {
  return supportLabels[filters.support_level] + ' · ' +
    (filters.status ? topicStatusLabels[filters.status] : 'Все статусы, кроме объединённых') + ' · ' +
    (filters.date_from || filters.date_to ? (filters.date_from || 'без начала') + ' — ' + (filters.date_to || 'без окончания') + ' (МСК)' : 'За всё время')
}

function requestFilters(filters: Filters): TopicFilters {
  return { support_level: filters.support_level, ...(filters.status && { status: filters.status }),
    ...(filters.date_from && { date_from: filters.date_from }), ...(filters.date_to && { date_to: filters.date_to }) }
}

export function TopicList() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [items, setItems] = useState<TopicListItem[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [revision, setRevision] = useState(0)
  const [receivedAt, setReceivedAt] = useState('')
  const [draft, setDraft] = useState<Filters>(defaults)
  const [applied, setApplied] = useState<Filters>(defaults)
  const [receivedFilters, setReceivedFilters] = useState<Filters>(defaults)
  const [validationError, setValidationError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const appliedRef = useRef<Filters>(defaults)

  useEffect(() => {
    const controller = new AbortController()
    controllerRef.current = controller
    async function load() {
      try {
        const result = await api.topics(controller.signal, requestFilters(applied))
        if (controller.signal.aborted) return
        setItems(result)
        setReceivedFilters(applied)
        setError(null)
        setReceivedAt(new Date().toLocaleTimeString('ru-RU'))
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof ApiError ? failure : new ApiError(0, {
          code: 'REQUEST_FAILED', message: 'Не удалось получить темы. Проверьте соединение и повторите загрузку.',
        }))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => { controller.abort() }
  }, [revision, applied])

  function loadFilters(next: Filters) {
    controllerRef.current?.abort()
    appliedRef.current = next
    setApplied(next)
    setLoading(true)
    setError(null)
    setRevision(value => value + 1)
  }

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (draft.date_from && draft.date_to && draft.date_from > draft.date_to) {
      setValidationError('Начало периода позже окончания. Исправьте даты и примените фильтры.')
      return
    }
    setValidationError(null)
    loadFilters({ ...draft })
  }

  function reset() {
    setDraft(defaults)
    setValidationError(null)
    loadFilters(defaults)
  }

  function updateTopic() {
    // Только сервер повторно подтверждает принадлежность темы текущему фильтру.
    // Это важно и при сбросе фильтра одновременно с сохранением решения.
    loadFilters(appliedRef.current)
  }

  const defaultResult = !receivedFilters.status && !receivedFilters.date_from && !receivedFilters.date_to &&
    receivedFilters.support_level === 'repeated'

  return <section className="topic-list" aria-labelledby="topic-list-title">
    <header className="topic-list-heading">
      <div><h2 id="topic-list-title">Темы из обращений</h2>
        <p>Фильтры этого списка независимы от периода запуска обработки. Повторяемость определяется за всё время. Даты включают обе границы по времени Москвы.</p>
      </div>
      <button type="button" disabled={loading} onClick={() => loadFilters(appliedRef.current)}>Обновить список</button>
    </header>
    <form className="topic-filters" aria-label="Фильтры тем" onSubmit={apply}>
      <label>Статус темы<select value={draft.status} onChange={event => setDraft({ ...draft, status: event.target.value as Filters['status'] })}>
        <option value="">Все, кроме объединённых</option>
        {(Object.keys(topicStatusLabels) as TopicStatus[]).map(status => <option key={status} value={status}>{topicStatusLabels[status]}</option>)}
      </select></label>
      <label>Повторяемость<select value={draft.support_level} onChange={event => setDraft({ ...draft, support_level: event.target.value as Filters['support_level'] })}>
        {(['repeated', 'single', 'all'] as const).map(value => <option key={value} value={value}>{supportLabels[value]}</option>)}
      </select></label>
      <label>Темы с даты<input type="date" value={draft.date_from} onChange={event => setDraft({ ...draft, date_from: event.target.value })} /></label>
      <label>Темы по дату<input type="date" value={draft.date_to} onChange={event => setDraft({ ...draft, date_to: event.target.value })} /></label>
      <div className="topic-actions"><button type="submit">Применить фильтры</button><button type="button" onClick={reset}>Сбросить фильтры</button></div>
    </form>
    <p><small>Изменения полей отправляются по кнопке «Применить фильтры». Для новых тем с одним обращением выберите «Все темы» или «Одиночные».</small></p>
    {validationError && <p role="alert" className="topic-list-error">{validationError}</p>}
    {loading && <p role="status">Загружаем темы…</p>}
    {error && <div className="topic-list-error" role="alert">
      <strong>Не удалось загрузить список тем.</strong><p>{error.message}</p>
      <small>{error.status > 0 ? 'HTTP ' + error.status + ' · ' : ''}{error.code}</small>
      <p>{error.status === 422 ? 'Проверьте значения фильтров и примените их заново. Введённые значения сохранены.' : 'Повторите загрузку кнопкой «Обновить список».'}</p>
    </div>}
    {error && items !== null && <p className="topic-list-stale">Показан последний полученный список. Обновление не подтверждено.</p>}
    {loading && items !== null && <p className="topic-list-stale">Пока показан предыдущий список с его фильтрами. Новый запрос ещё не завершён.</p>}
    {items !== null && <p className="topic-list-received"><small>Тем в полученном списке: {items.length} · Получено в {receivedAt}<br />
      Фильтры полученного списка: {filterSummary(receivedFilters)}</small></p>}
    {items?.length === 0 && !loading && <p>{error ? 'В последнем полученном списке тем не было.' : defaultResult
      ? 'Повторяющихся тем пока нет. Сервер вернул пустой список.'
      : 'По выбранным фильтрам тем нет. Сервер вернул пустой список.'}</p>}

    {items !== null && items.length > 0 && <ul aria-label="Сохранённые темы" className="topic-list-items">
      {items.map(item => <li key={item.topic.topic_id}>
        <article aria-labelledby={'topic-title-' + item.topic.topic_id}>
          <header className="topic-list-heading"><h3 id={'topic-title-' + item.topic.topic_id}>
            <button className="topic-open" type="button" aria-expanded={selectedId === item.topic.topic_id}
              onClick={() => setSelectedId(selectedId === item.topic.topic_id ? null : item.topic.topic_id)}>{item.topic.title}</button>
          </h3>
            <span className="topic-list-status">{topicStatusLabels[item.topic.status]}</span>
          </header>
          <p><small>ID темы: {item.topic.topic_id}</small></p>
          <div role="group" aria-label="Статистика за всё время"><h4>За всё время</h4>
            <dl className="topic-list-stats">
              <div><dt>Обращения</dt><dd>{item.stats.ticket_count}</dd></div>
              <div><dt>Уникальные заявители</dt><dd>{item.stats.requester_count ?? 'Нет данных'}</dd></div>
              <div><dt>Ответственный</dt><dd>{item.topic.owner ?? 'Не назначен'}</dd></div>
            </dl>
          </div>
          {item.period_stats !== null && <div role="group" aria-label="Статистика выбранного периода"><h4>За выбранный период</h4>
            <p>{item.period_stats.date_from || 'Без начальной даты'} — {item.period_stats.date_to || 'без конечной даты'} (МСК)</p>
            <dl className="topic-list-stats">
              <div><dt>Обращения</dt><dd>{item.period_stats.ticket_count}</dd></div>
              <div><dt>Уникальные заявители</dt><dd>{item.period_stats.requester_count ?? 'Нет данных'}</dd></div>
            </dl>
            <p><small>Источники и подробная карточка доступны за всё время.</small></p>
          </div>}
          <div className="topic-list-provenance">
            {item.synthetic && <p><strong>Синтетические данные</strong></p>}
            <p>Происхождение: {item.data_origin === 'authored' ? 'подготовлено вручную (authored)' : 'ответ модели (model)'}.</p>
            <p>Режим сервера: <strong>{item.model_mode}</strong>. {item.analysis_mode === null
              ? 'Результат модельного анализа не подтверждён.'
              : 'Режим анализа: ' + item.analysis_mode + (item.analysis_mode === 'replay' ? ' — сохранённые ответы' : '') + '.'}</p>
          </div>
          {item.pending_source_ids.length > 0 && <p className="topic-list-stale">Источников, ожидающих повторного анализа: {item.pending_source_ids.length}. Данные темы могут быть неполными.</p>}
        </article>
      </li>)}
    </ul>}
    {selectedId !== null && <>
      <p>Открытая карточка сохраняется при смене фильтров, даже если тема больше не входит в список. Черновик решения остаётся в ней.</p>
      <TopicDetail key={selectedId} topicId={selectedId} onClose={() => setSelectedId(null)} onTopicUpdated={updateTopic} />
    </>}
  </section>
}
