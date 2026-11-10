import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { EvidenceRef, ReviewItem } from '../api-types'
import { SourceViewer } from './SourceViewer'
import { ReviewDecisionForm } from './ReviewDecisionForm'

const statusLabels = { open: 'Связь не уточнена', resolved: 'Уточнено', insufficient_data: 'Недостаточно данных' }

export function ReviewPanel() {
  const [items, setItems] = useState<ReviewItem[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null)
  const [opened, setOpened] = useState<string[]>([])
  const [collapsed, setCollapsed] = useState<string[]>([])
  const [visible, setVisible] = useState(false)
  const [mutating, setMutating] = useState<string | null>(null)
  const mutationRef = useRef<string | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const readReview = useCallback((current: ReviewItem) => {
    requestRef.current?.abort(); setLoading(false)
    setItems(previous => previous?.map(entry => entry.review_id === current.review_id ? current : entry) ?? [current])
  }, [])
  const busyChange = useCallback((id: string, busy: boolean) => {
    if (busy && mutationRef.current !== null) return false
    if (!busy && mutationRef.current !== id) return false
    mutationRef.current = busy ? id : null; setMutating(mutationRef.current)
    if (busy) { requestRef.current?.abort(); setLoading(false) }
    return true
  }, [])
  useEffect(() => () => requestRef.current?.abort(), [])

  async function load() {
    if (mutationRef.current !== null) return
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller; setLoading(true); setError(null)
    try {
      const result = await api.reviews(controller.signal)
      if (!controller.signal.aborted) { setItems(result); setVisible(true) }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof ApiError ? failure : new ApiError(0, {
        code: 'REQUEST_FAILED', message: 'Не удалось загрузить очередь. Повторите чтение после проверки Backend.',
      }))
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setLoading(false) }
    }
  }
  return <section className="topic-list review-panel" aria-labelledby="review-title">
    <header className="topic-list-heading"><h2 id="review-title">Уточнение связей</h2>
      <button type="button" disabled={mutating !== null} aria-expanded={visible} onClick={() => { if (visible) setVisible(false); else if (items) setVisible(true); else void load() }}>{visible ? 'Скрыть результаты' : loading ? 'Обновить запрос очереди' : 'Загрузить очередь проверки'}</button>
      {items && visible && <button type="button" disabled={mutating !== null} onClick={() => { void load() }}>Обновить результаты</button>}</header>
    <p>Необязательное действие аналитика: уточнить, к какой теме относится найденная потребность. Это не назначение заявки сотруднику и не обязательная эскалация.</p>
    <p>Неуверенная классификация остаётся в «Неразобранном» и входит в аналитику без участия человека. Здесь можно добровольно исправить связь с темой; исходные данные и история сохраняются.</p>
    <p className="subtle">Отсутствие записей не доказывает успешность анализа. Ошибки и покрытие проверяются отдельно в разделе обработки.</p>
    {loading && <p role="status">Загружаем очередь проверки…</p>}
    {error && <div role="alert" className="topic-list-error"><p>{error.message}</p>
      <small>{error.status > 0 ? 'HTTP ' + error.status + ' · ' : ''}{error.code}</small></div>}
    {items && (loading || error) && <p className="topic-list-stale">Показана ранее полученная очередь; актуальность не подтверждена.</p>}
    {items && <div hidden={!visible}><p>Сохранённых случаев: {items.length}.</p>
      {items.length === 0 && <p>Backend вернул пустую очередь проверки. Проверьте отдельно состояние запуска и ошибки материалов.</p>}
      {items.map(item => <article key={item.review_id} aria-label={'Проверка ' + item.review_id}>
        <h3>{item.kind === 'assignment' ? 'Связь потребности с темой' : 'Возможные дубли тем'} · {statusLabels[item.status]}</h3>
        <p>ID: {item.review_id} · Версия: {item.version}</p><p className="topic-text">{item.reason}</p>
        {item.requires_recheck && <p className="topic-notice">Источник или каталог изменился. Требуется повторная проверка.</p>}
        <p>Потребности: {item.need_ids.join(', ') || 'Не указаны'}.</p>
        <p>Темы: {item.topic_ids.join(', ') || 'Не указаны'}. Кандидаты: {item.candidate_topic_ids.join(', ') || 'Не указаны'}.</p>
        <ul>{item.evidence_refs.map((ref, index) => <li key={index}>
          <blockquote>{ref.quote}</blockquote><p>Источник: {ref.source_id} · Фрагмент: {ref.fragment_id}</p>
          <button type="button" onClick={() => setEvidence(ref)}>Проверить цитату {index + 1} · {ref.source_id}</button>
        </li>)}</ul>
        {item.resolution && <div><p>Решение Backend: {item.resolution.action} · {item.resolution.author} · {new Date(item.resolution.resolved_at).toLocaleString('ru-RU')}</p>
          <p>{item.resolution.reason}</p>{item.resolution.topic_id && <p>Целевая тема: {item.resolution.topic_id}</p>}</div>}
        {item.kind === 'assignment' && <button type="button" disabled={mutating !== null} aria-expanded={opened.includes(item.review_id) && !collapsed.includes(item.review_id)}
          onClick={() => { if (!opened.includes(item.review_id)) setOpened(previous => [...previous, item.review_id]); else setCollapsed(previous => previous.includes(item.review_id) ? previous.filter(id => id !== item.review_id) : [...previous, item.review_id]) }}>{opened.includes(item.review_id) && !collapsed.includes(item.review_id) ? 'Свернуть решение и историю' : 'Решение и история Review'}</button>}
        {item.kind === 'assignment' && opened.includes(item.review_id) && <div hidden={collapsed.includes(item.review_id)}><ReviewDecisionForm
          item={item} stale={loading || error !== null || (mutating !== null && mutating !== item.review_id)}
          onRead={readReview} onBusyChange={busyChange} /></div>}
      </article>)}</div>}
    <p><a href="#topic-list-title">Открыть существующий список тем и карточки</a></p>
    {evidence && <SourceViewer key={evidence.source_id + '/' + evidence.fragment_id} sourceId={evidence.source_id}
      evidence={evidence} onClose={() => setEvidence(null)} />}
  </section>
}
