import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { TopicDetail } from '../api-types'
import { categoryLabels, priorityLabels } from '../processing-contracts'
import type { AssessmentSummary, Category, Priority } from '../processing-contracts'

export function TopicAssessments({ card }: { card: TopicDetail }) {
  const [summary, setSummary] = useState<AssessmentSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  async function load() {
    if (pending.current) return
    const controller = new AbortController(); pending.current = controller
    setBusy(true); setError('')
    try {
      const result = await api.assessmentSummary(card, controller.signal)
      if (!controller.signal.aborted) setSummary(result)
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Оценки не получены. Обновите карточку.')
    } finally { if (pending.current === controller) { pending.current = null; setBusy(false) } }
  }
  return <section aria-label="Категории и приоритеты темы">
    <h5>Категории и приоритеты темы</h5>
    <button disabled={busy} onClick={() => { void load() }}>{busy ? 'Читаем оценки…' : 'Показать категории и приоритеты'}</button>
    {error && <p role="alert" className="topic-list-error">{error}</p>}
    {summary && <>
      {(busy || error) && <p className="topic-list-stale">Показана последняя полученная оценка; актуальность не подтверждена.</p>}
      <p>Источников: {summary.source_count}; с потребностями без актуальной оценки: {summary.unassessed_source_count}.</p>
      <p>Каждая строка считает уникальные обращения. Категории и приоритеты могут пересекаться; суммы строк не равны общему числу обращений.</p>
      <ul>{Object.entries(summary.categories).map(([key, n]) => <li key={key}>{categoryLabels[key as Category]}: {n}</li>)}</ul>
      <ul>{Object.entries(summary.priorities).map(([key, n]) => <li key={key}>Приоритет «{priorityLabels[key as Priority]}»: {n}</li>)}</ul>
      <p>Отсутствующая оценка не означает обычный приоритет. Основания, неизвестности и происхождение — в источнике.</p>
    </>}
  </section>
}
