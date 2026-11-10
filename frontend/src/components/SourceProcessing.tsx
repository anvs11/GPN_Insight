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
  return <section className="source-analysis" aria-label="Покрытие и оценки источника">
    <section className="source-section"><h5>Покрытие материалов</h5>
    {!data && !error && <p role="status">Читаем сохранённую ревизию…</p>}
    {error && <p role="alert" className="topic-list-error">{error}</p>}
    {data && <>
      <p>Результат: {{ complete: 'полное покрытие', partial: 'частичное покрытие', failed: 'подготовка не удалась', empty: 'успешно обработано, пригодный текст пуст' }[data.result.state]}.</p>
      <dl className="source-coverage">{[
        ['Всего материалов', data.result.coverage.total], ['Подготовлено', data.result.coverage.successful],
        ['Ошибок', data.result.coverage.failed], ['Пустых', data.result.coverage.empty], ['С контекстом для анализа', data.result.coverage.usable],
      ].map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}</dl>
      <p className="source-section-note">Покрытие показывает, какие материалы удалось подготовить. Оно не измеряет качество вывода модели; ошибки вложений показаны отдельно.</p>
      {data.analysis_pending && <p className="topic-notice">Анализ новой ревизии ещё не опубликован. Прежние потребности могут ожидать обновления.</p>}
      {data.manual_recheck_required && <p className="topic-notice">Сохранённое ручное исправление требует повторной проверки; оно не отменено автоматически.</p>}
    </>}
    </section>
    <section className="source-section"><h5>Выделенные потребности и оценки</h5>
      {source.needs.length === 0 && <p>Сохранённых потребностей для оценки нет. Проверьте покрытие и ошибки; это не подтверждение отсутствия потребностей.</p>}
      <div className="source-needs">{source.needs.map(need => {
        const value = data?.assessments[need.need_id]
        return <article className="source-need" key={need.need_id}><h6>{need.statement}</h6>
          <p className="source-section-note">Сценарий: {need.scenario} · {need.explicit_request ? 'Явная просьба' : 'Наблюдение по источнику'}</p>
          {value ? <>
            <div className="source-assessment-labels"><span className="category-badge">Категория: {categoryLabels[value.assessment.category]}</span><span className={'priority-badge priority-' + value.assessment.priority}>Приоритет: {priorityLabels[value.assessment.priority]}</span></div>
            <dl className="source-reason"><div><dt>Почему такая категория и приоритет</dt><dd>{value.assessment.reason}</dd></div>
            {value.assessment.uncertainties.length > 0 && <div><dt>Что пока неизвестно</dt><dd>{value.assessment.uncertainties.join('; ')}</dd></div>}</dl>
            {value.assessment.priority === 'potentially_critical' && <p>Это возможный важный сигнал, не подтверждение ущерба или массовости.</p>}
            <div className="source-evidence"><strong>Основание в обращении</strong>{value.assessment.evidence_refs.map((ref, i) => <figure key={i}><blockquote>{ref.quote}</blockquote><figcaption>Фрагмент: {ref.fragment_id}</figcaption></figure>)}</div>
            <p className="source-section-note">Происхождение оценки: {value.metadata.origin === 'authored' ? 'подготовлено вручную (authored)' : 'ответ модели (model)'}.</p>
            <details className="source-model-details"><summary>Технические сведения об оценке</summary><p>Модель / профиль: {value.metadata.model_id}</p></details>
          </> : <p>Оценка отсутствует или не опубликована. Обычный приоритет не присвоен.</p>}
          {!value && <figure className="source-evidence"><blockquote>{need.evidence_quote}</blockquote><figcaption>Фрагмент: {need.fragment_id}</figcaption></figure>}
          {need.improvement_hypothesis !== null && <p>Гипотеза улучшения: {need.improvement_hypothesis}</p>}
          {need.uncertainty_reason !== null && <p className="topic-notice">Неопределённость: {need.uncertainty_reason}</p>}
        </article>
      })}</div>
    </section>
  </section>
}
