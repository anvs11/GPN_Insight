import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { SourceDetail } from '../api-types'
import { categoryLabels, priorityLabels } from '../processing-contracts'
import type { ProcessingDetail } from '../processing-contracts'

export function SourceProcessing({ source }: { source: SourceDetail }) {
  const [data, setData] = useState<ProcessingDetail | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const result = await api.processing(source, controller.signal)
        if (!controller.signal.aborted) setData(result)
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof ApiError && failure.status === 404
          ? 'Для этой версии нет новой ревизии обработки. Это не отсутствие потребностей или обычный приоритет.'
          : failure instanceof Error ? failure.message : 'Оценки не получены. Обновите источник.')
      }
    }
    void load()
    return () => controller.abort()
  }, [source])
  return <section aria-label="Покрытие и оценки источника">
    <h5>Покрытие и оценки</h5>
    {!data && !error && <p role="status">Читаем сохранённую ревизию…</p>}
    {error && <p role="alert" className="topic-list-error">{error}</p>}
    {data && <>
      <p>Результат: {{ complete: 'полное покрытие', partial: 'частичное покрытие', failed: 'подготовка не удалась', empty: 'успешно обработано, пригодный текст пуст' }[data.result.state]}.</p>
      <p>Материалов: {data.result.coverage.total}; успешно: {data.result.coverage.successful}; ошибок: {data.result.coverage.failed}; пустых: {data.result.coverage.empty}; с пригодным контекстом: {data.result.coverage.usable}.</p>
      <p>Это покрытие подготовки, а не качество модели. Ошибки вложений сохраняются отдельно.</p>
      {data.analysis_pending && <p className="topic-notice">Анализ новой ревизии ещё не опубликован. Прежние потребности могут ожидать обновления.</p>}
      {data.manual_recheck_required && <p className="topic-notice">Сохранённое ручное исправление требует повторной проверки; оно не отменено автоматически.</p>}
      {source.needs.length === 0 && <p>Сохранённых потребностей для оценки нет. Проверьте покрытие и ошибки; это не подтверждение отсутствия потребностей.</p>}
      <ul>{source.needs.map(need => {
        const value = data.assessments[need.need_id]
        return <li key={need.need_id}><strong>{need.statement}</strong>
          {value ? <>
            <p>Категория: {categoryLabels[value.assessment.category]} · Приоритет: {priorityLabels[value.assessment.priority]}</p>
            <p>{value.assessment.reason}</p>
            {value.assessment.priority === 'potentially_critical' && <p>Это возможный важный сигнал, не подтверждение ущерба или массовости.</p>}
            {value.assessment.uncertainties.length > 0 && <p>Неизвестности: {value.assessment.uncertainties.join('; ')}</p>}
            {value.assessment.evidence_refs.map((ref, i) => <blockquote key={i}>{ref.quote}<small> · {ref.fragment_id}</small></blockquote>)}
            <p>Происхождение оценки: {value.metadata.origin === 'authored' ? 'подготовлено вручную (authored)' : 'ответ модели (model)'} · {value.metadata.model_id}.</p>
          </> : <p>Оценка отсутствует или не опубликована. Обычный приоритет не присвоен.</p>}
        </li>
      })}</ul>
    </>}
  </section>
}
