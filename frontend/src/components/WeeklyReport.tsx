import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { CompletedWeek } from '../processing-contracts'

export function WeeklyReport() {
  const [saved, setSaved] = useState<{ period: CompletedWeek; text: string; url: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  const objectUrl = useRef('')
  useEffect(() => () => { pending.current?.abort(); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current) }, [])
  async function generate() {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true); setError('')
    try {
      const period = await api.weeklyPeriod(controller.signal)
      const text = await api.weeklyReport(controller.signal)
      if (!text.includes(`Неделя: ${period.period.date_from} — ${period.period.date_to}.`)) {
        throw new ApiError(200, { code: 'REPORT_PERIOD_CHANGED', message: 'Период отчёта изменился или не подтверждён. Повторите формирование.' })
      }
      if (!controller.signal.aborted) {
        if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
        objectUrl.current = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
        setSaved({ period, text, url: objectUrl.current })
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Не удалось получить отчёт. Повторите запрос.')
    } finally {
      if (pending.current === controller) { pending.current = null; setBusy(false) }
    }
  }
  return <section className="topic-list" aria-label="Недельный отчёт">
    <h2 id="weekly-report-title">Отчёт за завершённую неделю</h2>
    <p>Понедельник–воскресенье по Москве. Период определяет сервер; текущая неполная неделя не включается.</p>
    <p>Сводка формируется по запросу. Автоматическая рассылка не подключена. Replay/authored не измеряет качество живых моделей.</p>
    <button disabled={busy} onClick={() => { void generate() }}>{busy ? 'Формируем отчёт…' : 'Сформировать недельный отчёт'}</button>
    {error && <p role="alert" className="topic-list-error">{error}</p>}
    {saved && <>
      {(error || busy) && <p className="topic-list-stale">Ниже последний полученный отчёт. Актуальность нового запроса не подтверждена.</p>}
      <p>Неделя: <strong>{saved.period.period.date_from} — {saved.period.period.date_to}</strong> (Москва).</p>
      {!error && !busy && <a href={saved.url} download={`gpn-insight-${saved.period.period.date_from}.md`}>Скачать отчёт Markdown</a>}
      <details><summary>Прочитать полученный отчёт</summary><pre className="weekly-text">{saved.text}</pre></details>
    </>}
  </section>
}
