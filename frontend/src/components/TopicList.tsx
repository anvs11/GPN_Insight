import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { TopicDetail as Detail, TopicListItem } from '../api-types'
import { topicStatusLabels } from '../topic-labels'
import { TopicDetail } from './TopicDetail'
import './TopicList.css'

export function TopicList() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [items, setItems] = useState<TopicListItem[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [revision, setRevision] = useState(0)
  const [receivedAt, setReceivedAt] = useState('')
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    controllerRef.current = controller
    async function load() {
      try {
        const result = await api.topics(controller.signal)
        if (controller.signal.aborted) return
        setItems(result)
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
  }, [revision])

  function refresh() {
    controllerRef.current?.abort()
    setLoading(true)
    setError(null)
    setRevision(value => value + 1)
  }

  function updateTopic(updated: Detail) {
    // Старый снимок списка не должен отменить более позднее чтение карточки.
    controllerRef.current?.abort()
    setLoading(false)
    setItems(previous => previous?.map(current => current.topic.topic_id === updated.topic.topic_id ? updated : current) ?? null)
  }

  return <section className="topic-list" aria-labelledby="topic-list-title">
    <header className="topic-list-heading">
      <div><h2 id="topic-list-title">Темы из обращений</h2>
        <p>Сохранённые темы за всё время с двумя и более обращениями. Период запуска обработки не фильтрует этот список. Число обращений и число заявителей показаны отдельно.</p>
      </div>
      <button type="button" disabled={loading} onClick={refresh}>Обновить список</button>
    </header>

    {loading && <p role="status">Загружаем темы…</p>}
    {error && <div className="topic-list-error" role="alert">
      <strong>Не удалось загрузить список тем.</strong><p>{error.message}</p>
      <small>{error.status > 0 ? `HTTP ${error.status} · ` : ''}{error.code}</small>
      <p>Повторите загрузку кнопкой «Обновить список».</p>
    </div>}
    {error && items !== null && <p className="topic-list-stale">Показан последний полученный список. Обновление не подтверждено.</p>}
    {items !== null && <p className="topic-list-received"><small>Тем в полученном списке: {items.length} · Получено в {receivedAt}</small></p>}
    {items?.length === 0 && !loading && <p>{error
      ? 'В последнем полученном списке повторяющихся тем не было.'
      : 'Повторяющихся тем пока нет. Сервер вернул пустой список.'}</p>}

    {items !== null && items.length > 0 && <ul aria-label="Сохранённые темы" className="topic-list-items">
      {items.map(item => <li key={item.topic.topic_id}>
        <article aria-labelledby={`topic-title-${item.topic.topic_id}`}>
          <header className="topic-list-heading"><h3 id={`topic-title-${item.topic.topic_id}`}>
            <button className="topic-open" type="button" aria-expanded={selectedId === item.topic.topic_id}
              onClick={() => setSelectedId(selectedId === item.topic.topic_id ? null : item.topic.topic_id)}>{item.topic.title}</button>
          </h3>
            <span className="topic-list-status">{topicStatusLabels[item.topic.status]}</span>
          </header>
          <p><small>ID темы: {item.topic.topic_id}</small></p>
          <dl className="topic-list-stats">
            <div><dt>Обращения</dt><dd>{item.stats.ticket_count}</dd></div>
            <div><dt>Уникальные заявители</dt><dd>{item.stats.requester_count === null ? 'Нет данных' : item.stats.requester_count}</dd></div>
            <div><dt>Ответственный</dt><dd>{item.topic.owner ?? 'Не назначен'}</dd></div>
          </dl>
          <div className="topic-list-provenance">
            {item.synthetic && <p><strong>Синтетические данные</strong></p>}
            <p>Происхождение: {item.data_origin === 'authored' ? 'подготовлено вручную (authored)' : 'ответ модели (model)'}.</p>
            <p>Режим сервера: <strong>{item.model_mode}</strong>. {item.analysis_mode === null
              ? 'Результат модельного анализа не подтверждён.'
              : `Режим анализа: ${item.analysis_mode}${item.analysis_mode === 'replay' ? ' — сохранённые ответы' : ''}.`}</p>
          </div>
          {item.pending_source_ids.length > 0 && <p className="topic-list-stale">Источников, ожидающих повторного анализа: {item.pending_source_ids.length}. Данные темы могут быть неполными.</p>}
        </article>
        {selectedId === item.topic.topic_id && <TopicDetail topicId={selectedId} onClose={() => setSelectedId(null)}
          onTopicUpdated={updateTopic} />}
      </li>)}
    </ul>}
  </section>
}
