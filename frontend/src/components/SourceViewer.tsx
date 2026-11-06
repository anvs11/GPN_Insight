import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { EvidenceRef, SourceDetail } from '../api-types'
import { formatTopicDate } from '../topic-labels'

export function SourceViewer({ sourceId, evidence, onClose }: {
  sourceId: string; evidence?: EvidenceRef; onClose: () => void
}) {
  const [source, setSource] = useState<SourceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [revision, setRevision] = useState(0)
  const controllerRef = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    controllerRef.current = controller
    async function load() {
      try {
        const result = await api.source(sourceId, controller.signal)
        if (!controller.signal.aborted) { setSource(result); setError(null) }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof ApiError ? failure : new ApiError(0, {
          code: 'REQUEST_FAILED', message: 'Не удалось получить источник. Проверьте соединение и повторите чтение.',
        }))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [sourceId, revision])

  function refresh() {
    controllerRef.current?.abort()
    setLoading(true); setError(null); setRevision(value => value + 1)
  }
  const matchedFragment = evidence && source?.document?.fragments.find(fragment => fragment.fragment_id === evidence.fragment_id)
  const quoteFound = evidence && evidence.quote.length > 0 && matchedFragment?.text.includes(evidence.quote)
  const sourceIsCurrent = !loading && error === null

  return <section className="source-viewer" aria-label={`Источник ${sourceId}`}>
    <header className="topic-list-heading"><h4>Источник {sourceId}</h4><div className="topic-actions">
      <button type="button" disabled={loading} onClick={refresh}>Обновить источник</button>
      <button type="button" onClick={onClose}>Закрыть источник</button>
    </div></header>
    <p>Загружается текущая версия обращения. Она могла измениться после формирования карточки.</p>
    {loading && <p role="status">Загружаем источник…</p>}
    {error && <div role="alert" className="topic-list-error">
      <strong>{error.status === 404 ? 'Источник не найден.' : 'Не удалось загрузить источник.'}</strong>
      <p>{error.message}</p><small>{error.status > 0 ? `HTTP ${error.status} · ` : ''}{error.code}</small>
      <p>Повторите чтение кнопкой «Обновить источник» или закройте его. Карточка и список сохранены.</p>
    </div>}
    {source && <>
      {(error || loading) && <p className="topic-list-stale">Показан последний полученный источник. Актуальность пока не подтверждена.</p>}
      <p><strong>{source.synthetic ? 'Синтетический демонстрационный источник — не реальное обращение клиента.' : 'Источник не отмечен как синтетический.'}</strong></p>
      <p>Происхождение: {source.data_origin === 'authored' ? 'подготовлено вручную (authored)' : 'импортировано (imported)'}.</p>
      <dl className="topic-detail-stats"><div><dt>Версия источника</dt><dd>{source.source_version}</dd></div>
        <div><dt>Дата обращения (МСК)</dt><dd>{formatTopicDate(source.ticket.created_at)}</dd></div>
        <div><dt>Продукт</dt><dd>{source.ticket.product_id}</dd></div></dl>
      {source.requires_analysis && <p className="topic-notice">Источник ожидает анализа. Данные темы могут быть неполными.</p>}
      {evidence && <div className="topic-notice"><p>Цитата из карточки · Фрагмент: {evidence.fragment_id}</p><blockquote>{evidence.quote}</blockquote>
        <p>{!sourceIsCurrent ? 'Сверка цитаты с текущей версией источника не подтверждена. Дождитесь успешного обновления источника.'
          : quoteFound ? 'Цитата найдена в указанном фрагменте текущего источника. Это проверка совпадения текста, а не качества вывода.'
            : 'Цитата не подтверждена текущим фрагментом. Источник мог измениться или обработан не полностью. Обновите карточку и проверьте основание.'}</p>
      </div>}
      <h5>Исходный текст</h5>
      {source.synthetic ? <p className="topic-text">{source.ticket.text || 'Исходный текст отсутствует.'}</p>
        : <p>Оригинал несинтетического обращения не показывается: доступ к оригиналам требует отдельного согласования.</p>}
      <h5>Подготовленные фрагменты</h5>
      {source.document === null ? <p>Подготовленного документа пока нет. Это не означает отсутствия потребностей.</p> : <>
        <p>Версия документа: {source.document.version} · Версия нормализации: {source.document.normalization_version}</p>
        {source.document.fragments.length === 0 && <p>Фрагменты отсутствуют. Это не означает отсутствия потребностей.</p>}
        {source.document.fragments.map(fragment => <div key={fragment.fragment_id} className="source-fragment">
          <p>Фрагмент {fragment.fragment_id} · {fragment.kind === 'text' ? 'Текст' : fragment.kind === 'asr' ? 'Распознавание речи' : 'Описание изображения'}</p>
          {fragment.asset_id !== null && <p>Вложение: {fragment.asset_id}</p>}
          {(fragment.start_ms !== null || fragment.end_ms !== null) && <p>Метки сервера (мс): {fragment.start_ms ?? 'нет данных'} — {fragment.end_ms ?? 'нет данных'}</p>}
          <p className="topic-text">{fragment.text || 'Текст фрагмента отсутствует.'}</p>
          {fragment.warning && <p className="topic-notice">Предупреждение: {fragment.warning}</p>}
        </div>)}
        {source.document.errors.length > 0 && <div className="topic-list-error"><h5>Ошибки обработки материалов</h5><ul>
          {source.document.errors.map((failure, index) => <li key={index}>{failure.stage} · {failure.code} · {failure.asset_id ?? 'без вложения'}: {failure.message}</li>)}
        </ul><p>Покрытие неполное; ошибка не означает отсутствия потребностей.</p></div>}
      </>}
      <h5>Выделенные потребности</h5>
      {source.needs.length === 0 ? <p>Сохранённых потребностей нет. Это не подтверждает, что потребностей в обращении нет.</p> : <ul>{source.needs.map(need => <li key={need.need_id}>
        <p className="topic-text">{need.statement}</p><p>Сценарий: {need.scenario} · {need.explicit_request ? 'Явная просьба' : 'Наблюдение по источнику'}</p>
        <blockquote>{need.evidence_quote}</blockquote><p>Фрагмент: {need.fragment_id}</p>
        {need.improvement_hypothesis !== null && <p className="topic-text">Гипотеза улучшения: {need.improvement_hypothesis}</p>}
        {need.uncertainty_reason !== null && <p className="topic-notice">Неопределённость: {need.uncertainty_reason}</p>}
      </li>)}</ul>}
      <h5>Вложения</h5>
      {source.ticket.assets.length === 0 ? <p>Вложений нет.</p> : <><ul>{source.ticket.assets.map(asset => <li key={asset.asset_id}>
        {asset.asset_id} · {asset.kind === 'audio' ? 'Аудио' : 'Изображение'} · {asset.mime_type}
      </li>)}</ul><p>Просмотр файлов пока недоступен. Можно проверить подготовленные фрагменты и ошибки обработки.</p></>}
    </>}
  </section>
}
