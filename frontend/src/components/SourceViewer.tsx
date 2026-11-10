import { useEffect, useId, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { EvidenceRef, SourceDetail } from '../api-types'
import { formatTopicDate } from '../topic-labels'
import { SourceProcessing } from './SourceProcessing'
import { MediaAsset } from './MediaAsset'
import { SourceAge } from './SourceAge'

export function SourceViewer({ sourceId, evidence, onClose }: {
  sourceId: string; evidence?: EvidenceRef; onClose: () => void
}) {
  const [source, setSource] = useState<SourceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [revision, setRevision] = useState(0)
  const [textOpen, setTextOpen] = useState(false)
  const textId = useId()
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
        <SourceAge createdAt={source.ticket.created_at} />
        <div><dt>Продукт</dt><dd>{source.ticket.product_id}</dd></div></dl>
      {source.requires_analysis && <p className="topic-notice">Источник ожидает анализа. Данные темы могут быть неполными.</p>}
      {source.manual_recheck_required && <p className="topic-notice">Ручное исправление требует повторной проверки после обновления анализа.</p>}
      <section className="source-section source-original">
        <button className="source-text-toggle" type="button" aria-expanded={textOpen} aria-controls={textId} onClick={() => setTextOpen(value => !value)}>{textOpen ? 'Скрыть текст обращения и расшифровки' : 'Показать текст обращения и расшифровки'}</button>
        <div id={textId} hidden={!textOpen}>
          <h5>Текст обращения и расшифровки</h5>
          {source.synthetic && source.original_access !== 'restricted' ? <p className="topic-text source-original-text">{source.ticket.text || 'Исходный текст отсутствует.'}</p>
            : <p>Оригинал несинтетического обращения не показывается: доступ к оригиналам требует отдельного согласования.</p>}
          {source.document?.fragments.filter(fragment => fragment.kind !== 'text').map((fragment, index) => <article className="source-transcript" key={fragment.fragment_id}>
            <h6>{fragment.kind === 'asr' ? 'Расшифровка аудио' : 'Описание изображения'} {index + 1}</h6>
            <p className="source-section-note">Вложение: {fragment.asset_id ?? 'не указано'}{fragment.start_ms !== null || fragment.end_ms !== null ? ` · Метки сервера (мс): ${fragment.start_ms ?? 'нет данных'} — ${fragment.end_ms ?? 'нет данных'}` : ''}</p>
            <p className="topic-text">{fragment.text || 'Пригодный текст отсутствует.'}</p>
          </article>)}
          {!source.document && <p>Распознавание ещё не подготовлено. После обработки здесь появятся доступные расшифровки.</p>}
        </div>
      </section>
      {sourceIsCurrent && source.current_result_revision && <SourceProcessing key={source.current_result_revision} source={source} />}
      {!source.current_result_revision && <p>Отдельной оценки категории/приоритета для этого источника нет. Отсутствие оценки не означает обычный приоритет.</p>}
      {evidence && <section className="source-section source-evidence"><h5>Проверка цитаты из карточки</h5><figure><blockquote>{evidence.quote}</blockquote><figcaption>Фрагмент: {evidence.fragment_id}</figcaption></figure>
        <p>{!sourceIsCurrent ? 'Сверка цитаты с текущей версией источника не подтверждена. Дождитесь успешного обновления источника.'
          : quoteFound ? 'Цитата найдена в указанном фрагменте текущего источника. Это проверка совпадения текста, а не качества вывода.'
            : 'Цитата не подтверждена текущим фрагментом. Источник мог измениться или обработан не полностью. Обновите карточку и проверьте основание.'}</p>
      </section>}
      {source.document && source.document.errors.length > 0 && <section className="source-section topic-list-error"><h5>Ошибки обработки материалов</h5><ul>
        {source.document.errors.map((failure, index) => <li key={index}>{failure.stage} · {failure.code} · {failure.asset_id ?? 'без вложения'}: {failure.message}</li>)}
      </ul><p>Покрытие неполное; ошибка не означает отсутствия потребностей.</p></section>}
      {(!source.current_result_revision || !sourceIsCurrent) && <section className="source-section"><h5>Выделенные потребности</h5>
      {source.needs.length === 0 ? <p>Сохранённых потребностей нет. Это не подтверждает, что потребностей в обращении нет.</p> : <div className="source-needs">{source.needs.map(need => <article className="source-need" key={need.need_id}>
        <h6>{need.statement}</h6><p>Сценарий: {need.scenario} · {need.explicit_request ? 'Явная просьба' : 'Наблюдение по источнику'}</p>
        <figure className="source-evidence"><blockquote>{need.evidence_quote}</blockquote><figcaption>Фрагмент: {need.fragment_id}</figcaption></figure>
        {need.improvement_hypothesis !== null && <p className="topic-text">Гипотеза улучшения: {need.improvement_hypothesis}</p>}
        {need.uncertainty_reason !== null && <p className="topic-notice">Неопределённость: {need.uncertainty_reason}</p>}
      </article>)}</div>}</section>}
      <section className="source-section"><h5>Вложения</h5>
      {source.ticket.assets.length === 0 ? <p>Вложений нет.</p> : <><ul className="source-assets">{source.ticket.assets.map(asset => <li key={asset.asset_id}>
        <strong>{asset.kind === 'audio' ? 'Аудио' : 'Изображение'}</strong> · {asset.mime_type}<p className="source-section-note">ID: {asset.asset_id}</p>
        {sourceIsCurrent && <MediaAsset key={`${source.source_version}-${asset.asset_id}`} source={source} asset={asset} />}
      </li>)}</ul>{!sourceIsCurrent && <p>Просмотр файлов доступен после успешного обновления источника.</p>}</>}
      </section>
      <details className="source-section source-technical"><summary>Подготовленные фрагменты — технические сведения</summary>
        <p className="source-section-note">Фрагмент — часть текста, расшифровки или описания изображения, на которую ссылается найденная потребность. Его ID нужен для проверки цитат; это не отдельное обращение.</p>
      {source.document === null ? <p>Подготовленного документа пока нет. Это не означает отсутствия потребностей.</p> : <>
        <p>Версия документа: {source.document.version} · Версия нормализации: {source.document.normalization_version}</p>
        {source.document.fragments.length === 0 && <p>Фрагменты отсутствуют. Это не означает отсутствия потребностей.</p>}
        {source.document.fragments.map(fragment => <div key={fragment.fragment_id} className="source-fragment">
          <p>Фрагмент {fragment.fragment_id} · {fragment.kind === 'text' ? 'Текст' : fragment.kind === 'asr' ? 'Распознавание речи' : 'Описание изображения'}</p>
          {fragment.asset_id !== null && <p>Вложение: {fragment.asset_id}</p>}
          {(fragment.start_ms !== null || fragment.end_ms !== null) && <p>Метки сервера (мс): {fragment.start_ms ?? 'нет данных'} — {fragment.end_ms ?? 'нет данных'}</p>}
          <p className="topic-text">{fragment.text || 'Текст фрагмента отсутствует.'}</p>
          {fragment.warning && <div className="topic-notice"><strong>Ограничение подготовки</strong><p>{fragment.warning}</p></div>}
        </div>)}
      </>}
      </details>
    </>}
  </section>
}
