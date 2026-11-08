import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { EvidenceRef, ReviewItem } from '../api-types'
import { SourceViewer } from './SourceViewer'

const statusLabels = { open: 'Ожидает проверки', resolved: 'Разрешено', insufficient_data: 'Недостаточно данных' }

export function ReviewPanel() {
  const [items, setItems] = useState<ReviewItem[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  useEffect(() => () => requestRef.current?.abort(), [])

  async function load() {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller; setLoading(true); setError(null)
    try {
      const result = await api.reviews(controller.signal)
      if (!controller.signal.aborted) setItems(result)
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof ApiError ? failure : new ApiError(0, {
        code: 'REQUEST_FAILED', message: 'Не удалось загрузить очередь. Повторите чтение после проверки Backend.',
      }))
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setLoading(false) }
    }
  }
  return <section className="topic-list review-panel" aria-labelledby="review-title">
    <header className="topic-list-heading"><h2 id="review-title">Проверка спорных результатов</h2>
      <button type="button" onClick={() => { void load() }}>{loading ? 'Обновить запрос очереди' : 'Загрузить очередь проверки'}</button></header>
    <p>Здесь показаны сохранённые Backend случаи неоднозначного назначения потребностей и проверки дублей тем.</p>
    <p>Список загружается по кнопке. Отсутствие записей не доказывает успешность анализа. Ручное разрешение этих случаев пока не подключено в интерфейсе.</p>
    {loading && <p role="status">Загружаем очередь проверки…</p>}
    {error && <div role="alert" className="topic-list-error"><p>{error.message}</p>
      <small>{error.status > 0 ? 'HTTP ' + error.status + ' · ' : ''}{error.code}</small></div>}
    {items && (loading || error) && <p className="topic-list-stale">Показана ранее полученная очередь; актуальность не подтверждена.</p>}
    {items && <><p>Сохранённых случаев: {items.length}.</p>
      {items.length === 0 && <p>Backend вернул пустую очередь проверки. Проверьте отдельно состояние запуска и ошибки материалов.</p>}
      {items.map(item => <article key={item.review_id} aria-label={'Проверка ' + item.review_id}>
        <h3>{item.kind === 'assignment' ? 'Назначение потребности' : 'Проверка дублей тем'} · {statusLabels[item.status]}</h3>
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
      </article>)}</>}
    <p><a href="#topic-list-title">Открыть существующий список тем и карточки</a></p>
    {evidence && <SourceViewer key={evidence.source_id + '/' + evidence.fragment_id} sourceId={evidence.source_id}
      evidence={evidence} onClose={() => setEvidence(null)} />}
  </section>
}
