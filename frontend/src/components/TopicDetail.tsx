import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { EvidenceRef, PassportExample, TopicDetail as Detail } from '../api-types'
import { formatTopicDate, topicStatusLabels } from '../topic-labels'
import { SourceViewer } from './SourceViewer'
import { DecisionForm } from './DecisionForm'
import { PassportForm } from './PassportForm'
import './TopicDetail.css'

export function TopicDetail({ topicId, onClose, onTopicUpdated }: { topicId: string; onClose: () => void; onTopicUpdated?: (value: Detail) => void }) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [revision, setRevision] = useState(0)
  const [selection, setSelection] = useState<{ id: string; evidence?: EvidenceRef } | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const [decisionBusy, setDecisionBusy] = useState(false)
  const [passportBusy, setPassportBusy] = useState(false)
  const [editingPassport, setEditingPassport] = useState(false)
  const mutationRef = useRef<'decision' | 'passport' | null>(null)
  const busy = decisionBusy || passportBusy

  function mutationBusy(kind: 'decision' | 'passport', value: boolean) {
    // Общий ref блокирует две записи даже до следующего рендера React.
    if (value && mutationRef.current !== null) return false
    if (!value && mutationRef.current !== kind) return false
    mutationRef.current = value ? kind : null
    if (kind === 'decision') setDecisionBusy(value)
    else setPassportBusy(value)
    return true
  }
  function readCard(value: Detail) {
    controllerRef.current?.abort()
    setLoading(false); setDetail(value); setError(null); onTopicUpdated?.(value)
  }

  useEffect(() => {
    const controller = new AbortController()
    controllerRef.current = controller
    async function load() {
      try {
        const result = await api.topic(topicId, controller.signal)
        if (!controller.signal.aborted) { setDetail(result); setError(null) }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof ApiError ? failure : new ApiError(0, {
          code: 'REQUEST_FAILED', message: 'Не удалось получить карточку. Проверьте соединение и повторите чтение.',
        }))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [topicId, revision])

  function refresh() {
    if (busy) return
    controllerRef.current?.abort()
    setLoading(true); setError(null); setSelection(null)
    setRevision(value => value + 1)
  }

  function evidenceList(refs: EvidenceRef[]) {
    return refs.length === 0 ? <p>Подтверждающие цитаты отсутствуют.</p> : <ul className="topic-evidence">
      {refs.map((ref, index) => <li key={`${ref.source_id}-${ref.fragment_id}-${index}`}>
        <blockquote>{ref.quote}</blockquote>
        <p>Обращение: {ref.source_id} · Фрагмент: {ref.fragment_id}</p>
        <button type="button" onClick={() => setSelection({ id: ref.source_id, evidence: ref })}>Проверить источник {ref.source_id}</button>
      </li>)}
    </ul>
  }

  function examples(title: string, items: PassportExample[]) {
    return <details><summary>{title} ({items.length})</summary>
      {items.length === 0 ? <p>Примеры не заданы.</p> : items.map((item, index) => <div key={index}>
        <p className="topic-text">{item.text}</p>{evidenceList(item.evidence_refs)}
      </div>)}
    </details>
  }

  return <section className="topic-detail" aria-label="Карточка темы">
    <header className="topic-list-heading"><h3>Карточка темы</h3><div className="topic-actions">
      <button type="button" disabled={loading || busy} onClick={refresh}>Обновить карточку</button>
      <button type="button" disabled={busy} onClick={onClose}>Закрыть карточку</button>
    </div></header>
    {loading && <p role="status">Загружаем карточку…</p>}
    {error && <div role="alert" className="topic-list-error">
      <strong>{error.status === 404 ? 'Тема не найдена.' : 'Не удалось загрузить карточку.'}</strong>
      <p>{error.message}</p><small>{error.status > 0 ? `HTTP ${error.status} · ` : ''}{error.code}</small>
      <p>{error.status === 404 ? 'Обновите список тем или закройте карточку.' : 'Повторите чтение кнопкой «Обновить карточку». Список тем сохранён.'}</p>
    </div>}
    {detail && <>
      {(error || loading) && <p className="topic-list-stale">Показана последняя полученная карточка. Актуальность пока не подтверждена.</p>}
      <h4>{detail.topic.title}</h4>
      {detail.synthetic && <p className="topic-notice"><strong>Синтетические демонстрационные данные — не статистика компании.</strong></p>}
      <p>Происхождение: {detail.data_origin === 'authored' ? 'подготовлено вручную (authored)' : 'ответ модели (model)'}.
        {' '}Режим сервера: <strong>{detail.model_mode}</strong>. {detail.analysis_mode === null ? 'Результат модельного анализа не подтверждён.' : `Режим анализа: ${detail.analysis_mode}${detail.analysis_mode === 'replay' ? ' — сохранённые ответы' : ''}.`}</p>
      <p>Показатели и источники карточки — за всё время. Период списка их не ограничивает.</p>
      <dl className="topic-detail-stats">
        <div><dt>ID темы</dt><dd>{detail.topic.topic_id}</dd></div>
        <div><dt>Статус</dt><dd>{topicStatusLabels[detail.topic.status]}</dd></div>
        <div><dt>Ответственный</dt><dd>{detail.topic.owner ?? 'Не назначен'}</dd></div>
        <div><dt>Версия темы</dt><dd>{detail.topic.version}</dd></div>
        <div><dt>Обращения за всё время</dt><dd>{detail.stats.ticket_count}</dd></div>
        <div><dt>Уникальные заявители</dt><dd>{detail.stats.requester_count ?? 'Нет данных'}</dd></div>
        <div><dt>Поддержка источниками</dt><dd>{detail.stats.support_level === 'repeated' ? 'Повторяющаяся потребность' : 'Одиночный сигнал'}</dd></div>
        <div><dt>Первое обращение (МСК)</dt><dd>{formatTopicDate(detail.stats.first_seen)}</dd></div>
        <div><dt>Последнее обращение (МСК)</dt><dd>{formatTopicDate(detail.stats.last_seen)}</dd></div>
        <div><dt>Новых обращений после решения</dt><dd>{detail.stats.new_ticket_count_since_decision}</dd></div>
        <div><dt>Обращений после реализации</dt><dd>{detail.stats.post_implementation_ticket_count}</dd></div>
        <div><dt>Нужен пересмотр</dt><dd>{detail.stats.review_due ? 'Да' : 'Нет'}</dd></div>
      </dl>
      {detail.topic.canonical_topic_id !== null && <p>Объединена с темой: {detail.topic.canonical_topic_id}</p>}
      {detail.pending_source_ids.length > 0 && <p className="topic-list-stale">Ожидают повторного анализа: {detail.pending_source_ids.join(', ')}. Карточка может быть неполной.</p>}
      <h4>Описание потребности</h4>
      <p className="topic-text">{detail.topic.passport.need_statement || 'Описание не заполнено.'}</p>
      <p className="topic-text">{detail.topic.summary || 'Краткое описание отсутствует.'}</p>
      <dl><dt>Сценарий</dt><dd className="topic-text">{detail.topic.passport.scenario || 'Не задан'}</dd>
        <dt>Границы потребности</dt><dd className="topic-text">{detail.topic.passport.scope || 'Не заданы'}</dd>
        <dt>Подтверждение границ</dt><dd>{detail.topic.passport.scope_confirmed ? 'Подтверждены' : 'Не подтверждены'}</dd>
        <dt>Версия паспорта</dt><dd>{detail.topic.passport.version}</dd></dl>
      {examples('Примеры включения в тему', detail.topic.passport.inclusion_examples)}
      {examples('Примеры исключения из темы', detail.topic.passport.exclusion_examples)}
      {!editingPassport && <button type="button" disabled={loading || busy || error !== null} onClick={() => setEditingPassport(true)}>Редактировать паспорт и посмотреть историю</button>}
      {editingPassport && <PassportForm detail={detail} stale={loading || error !== null} disabled={decisionBusy}
        onRead={readCard} onBusyChange={value => mutationBusy('passport', value)} />}
      <h4>Количество обращений по неделям</h4><p>Начало недели — дата, указанная сервером. Счётчики рассчитаны по сохранённым записям.</p>
      {Object.keys(detail.stats.weekly_counts).length === 0 ? <p>Данных по неделям нет.</p> : <ul>
        {Object.entries(detail.stats.weekly_counts).sort(([a], [b]) => a.localeCompare(b)).map(([week, count]) => <li key={week}>{week}: {count}</li>)}
      </ul>}
      <h4>Подтверждающие обращения</h4>{evidenceList(detail.topic.evidence)}
      {detail.sources.length === 0 ? <p>Сведения об источниках отсутствуют.</p> : <ul className="topic-source-list">
        {detail.sources.map(source => <li key={source.ticket.source_id}>
          <button type="button" onClick={() => setSelection({ id: source.ticket.source_id })}>Открыть обращение {source.ticket.source_id}</button>
          {' '}Версия в карточке: {source.source_version}.{source.synthetic && ' Синтетический источник.'}
          {source.requires_analysis && ' Требуется повторный анализ.'}
        </li>)}
      </ul>}
      {selection && <SourceViewer key={`${selection.id}-${selection.evidence?.fragment_id ?? ''}-${selection.evidence?.quote ?? ''}`}
        sourceId={selection.id} evidence={selection.evidence} onClose={() => setSelection(null)} />}
      <DecisionForm key={detail.topic.topic_id} detail={detail} stale={loading || error !== null} disabled={passportBusy}
        onBusyChange={value => mutationBusy('decision', value)} onRead={readCard} />
      <details><summary>История решений ({detail.history.length})</summary>
        {detail.history.length === 0 ? <p>Сохранённых решений пока нет.</p> : <ol>{detail.history.map(event => <li key={event.event_id}>
          <p>{formatTopicDate(event.occurred_at)} МСК · {topicStatusLabels[event.previous_status]} → {topicStatusLabels[event.new_status]} · Версия темы: {event.topic_version}</p>
          <p>Ответственный: {event.owner}</p><p className="topic-text">Причина: {event.reason}</p>
          {event.existing_issue_ref !== null && <p>Ссылка на задачу: {event.existing_issue_ref}</p>}
          {event.review_after !== null && <p>Пересмотреть после: {event.review_after}</p>}
          {event.implemented_at !== null && <p>Реализовано: {formatTopicDate(event.implemented_at)} МСК</p>}
          {event.implementation_comment !== null && <p className="topic-text">Комментарий: {event.implementation_comment}</p>}
        </li>)}</ol>}
      </details>
    </>}
  </section>
}
