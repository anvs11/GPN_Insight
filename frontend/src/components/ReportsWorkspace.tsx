import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ArchivePage, ReportSnapshot, WorkspaceCard } from '../workspace-contracts'
import { cardKey } from '../workspace-labels'
import { formatTopicDate } from '../topic-labels'
import { ReportDocument } from './ReportDocument'
import { WorkspaceDrawer } from './WorkspaceDrawer'

function Downloads({ snapshot, onView, onPrint }: { snapshot: ReportSnapshot; onView: () => void; onPrint: () => void }) {
  const [urls, setUrls] = useState<{ md: string; json: string } | null>(null)
  const [pdfBusy, setPdfBusy] = useState(false), [pdfError, setPdfError] = useState('')
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  async function pdf() {
    if (pending.current) return
    const controller = new AbortController(); pending.current = controller; setPdfBusy(true); setPdfError('')
    try {
      const file = await api.reportPdf(snapshot.report_id, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(file), link = document.createElement('a')
      link.href = url; link.download = `gpn-report-${snapshot.report_id}.pdf`; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (failure) { if (!controller.signal.aborted) setPdfError(failure instanceof Error ? failure.message : 'PDF не получен.') }
    finally { if (pending.current === controller) { pending.current = null; setPdfBusy(false) } }
  }
  useEffect(() => {
    const md = URL.createObjectURL(new Blob([snapshot.markdown], { type: 'text/markdown;charset=utf-8' }))
    const json = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json;charset=utf-8' }))
    // Object URLs — внешний ресурс; создаём/освобождаем только в effect, не во время render.
    // oxlint-disable-next-line react/set-state-in-effect
    setUrls({ md, json })
    return () => { URL.revokeObjectURL(md); URL.revokeObjectURL(json) }
  }, [snapshot])
  return <div className="download-actions"><button className="button-secondary" onClick={onView}>Просмотр</button>{urls && <><a className="button-secondary" href={urls.md} download={`gpn-report-${snapshot.report_id}.md`}>Скачать Markdown</a>
    <a className="button-secondary" href={urls.json} download={`gpn-report-${snapshot.report_id}.json`}>Скачать JSON</a></>}
    <button className="button-secondary" disabled={pdfBusy} onClick={() => { void pdf() }}>{pdfBusy ? 'Готовим PDF…' : 'Сохранить PDF'}</button><button className="button-secondary" onClick={onPrint}>Печать</button>
    {pdfError && <p role="alert" className="workspace-error">{pdfError}</p>}</div>
}

export function ReportsWorkspace({ selected, onRemove, onRefresh, onMonitoring, onBusy }: {
  selected: WorkspaceCard[]; onRemove: (card: WorkspaceCard) => void; onRefresh: (cards: WorkspaceCard[]) => void; onMonitoring: () => void; onBusy: (busy: boolean) => void
}) {
  const [title, setTitle] = useState('Обзор для руководителя'), [summary, setSummary] = useState(''), [comment, setComment] = useState('')
  const [snapshot, setSnapshot] = useState<ReportSnapshot | null>(null), [archive, setArchive] = useState<ArchivePage | null>(null)
  const [error, setError] = useState(''), [archiveError, setArchiveError] = useState(''), [busy, setBusy] = useState(false), [reading, setReading] = useState(false)
  const [offset, setOffset] = useState(0), [revision, setRevision] = useState(0)
  const locked = useRef(false), request = useRef<AbortController | null>(null)
  const [message, setMessage] = useState('')
  const [schedule, setSchedule] = useState<Awaited<ReturnType<typeof api.weeklySchedule>> | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void api.weeklySchedule(controller.signal).then(value => { if (!controller.signal.aborted) setSchedule(value) }).catch(() => { if (!controller.signal.aborted) setSchedule(null) })
    const timer = window.setInterval(() => setRevision(value => value + 1), 60_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [revision])
  const [viewing, setViewing] = useState<string | null>(null), [printing, setPrinting] = useState(false)
  useEffect(() => {
    if (!printing || !snapshot || viewing !== snapshot.report_id || reading) return
    const frame = requestAnimationFrame(() => { window.print(); setPrinting(false) })
    return () => cancelAnimationFrame(frame)
  }, [printing, snapshot, viewing, reading])
  function showSnapshot(print = false) { if (snapshot) { setViewing(snapshot.report_id); setPrinting(print) } }
  useEffect(() => {
    const controller = new AbortController()
    void api.reportArchive(offset, controller.signal).then(result => { if (!controller.signal.aborted) { setArchive(result); setArchiveError('') } })
      .catch(failure => { if (!controller.signal.aborted) setArchiveError(failure instanceof Error ? failure.message : 'Архив недоступен.') })
    return () => controller.abort()
  }, [offset, revision])
  useEffect(() => () => request.current?.abort(), [])

  async function save(weekly: boolean) {
    if (locked.current) return
    locked.current = true; onBusy(true); setBusy(true); setError(''); setMessage('')
    request.current?.abort(); setReading(false)
    try {
      const result = await api.saveReport(weekly ? null : { title, summary, comment,
        selections: selected.map(({ kind, id, version, revision }) => ({ kind, id, version, revision })) })
      setSnapshot(result); setMessage('Отчёт сохранён на сервере. Его содержимое зафиксировано и останется после перезапуска.')
      setOffset(0); setRevision(n => n + 1)
    } catch (failure) { setError((failure instanceof Error ? failure.message : 'Сохранение не подтверждено.') + ' Черновик сохранён на этой странице. При потере связи проверьте архив перед повтором.') }
    finally { locked.current = false; setBusy(false); onBusy(false) }
  }
  async function open(id: string) {
    if (locked.current) return
    request.current?.abort(); const controller = new AbortController(); request.current = controller
    setViewing(id); setReading(true); setError(''); setMessage(''); setPrinting(false)
    try { const result = await api.archivedReport(id, controller.signal); if (!controller.signal.aborted) setSnapshot(result) }
    catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Отчёт не получен.') }
    finally { if (!controller.signal.aborted) setReading(false) }
  }
  async function refreshSelection() {
    if (locked.current) return
    locked.current = true; onBusy(true); setBusy(true); setError('')
    try {
      const values = await Promise.all(selected.map(card => api.monitoringCard(card.kind, card.id)))
      onRefresh(values); setMessage('Версии выбранных объектов обновлены. Проверьте подборку перед сохранением.')
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось обновить версии. Прежняя подборка сохранена.') }
    finally { locked.current = false; setBusy(false); onBusy(false) }
  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">РЕЗУЛЬТАТЫ ДЛЯ РУКОВОДИТЕЛЯ</span><h1>Отчёты</h1><p>Недельная картина и важные сигналы в вашей подборке.</p></div><button className="button-secondary" disabled={busy} onClick={onMonitoring}>← К мониторингу</button></div>
    <section className="weekly-banner"><div><span className="eyebrow">ЗАВЕРШЁННАЯ НЕДЕЛЯ</span><h2>Неделя в одном отчёте</h2><p>Понедельник–воскресенье по Москве. {schedule?.enabled ? `Автоматически по понедельникам в 00:00 МСК при работающем сервере. Следующее закрытие: ${formatTopicDate(schedule.next_run_at)} МСК.` : schedule ? 'Автосохранение отключено на сервере.' : 'Состояние автосохранения пока не подтверждено.'} Пропущена полночь — последняя завершённая неделя сохранится после запуска. Рассылки нет.</p>{schedule?.last_error && <p role="alert">Автосохранение не удалось; сервер повторит попытку. Можно сохранить вручную.</p>}</div><button className="button-primary" disabled={busy} onClick={() => { void save(true) }}>Сформировать и сохранить</button></section>
    <div className="reports-columns"><div>
      <section className="surface report-composer" aria-label="Редактор подборки"><div className="section-heading"><div><span className="eyebrow">ВАША ПОДБОРКА</span><h2>Соберите контекст для решения</h2></div><span className="count-chip">{selected.length} / 30</span></div>
        <fieldset disabled={busy}><label>Название отчёта<input value={title} maxLength={160} onChange={e => setTitle(e.target.value)} /></label>
          <label>Краткое резюме<textarea value={summary} maxLength={4000} rows={3} placeholder="Что руководителю важно знать об этой подборке?" onChange={e => setSummary(e.target.value)} /></label>
          <label>Комментарий автора<textarea value={comment} maxLength={8000} rows={3} placeholder="Контекст, наблюдения и предлагаемые действия" onChange={e => setComment(e.target.value)} /></label>
          <p className="subtle">Комментарии к отчёту не изменяют оценки и решения по темам. Черновик хранится в этой вкладке до перезагрузки; сохранённые снимки — на сервере.</p>
          <div className="section-heading"><h3>Выбранные материалы · {selected.length}</h3><button className="text-button" disabled={!selected.length} onClick={() => { void refreshSelection() }}>Обновить версии</button></div>
          {!selected.length && <div className="empty-state"><h3>Начните с мониторинга</h3><p>Отметьте нужные темы или обращения галочкой «В отчёт».</p><button className="button-secondary" onClick={onMonitoring}>Выбрать карточки</button></div>}
          <ul className="selected-materials">{selected.map(card => <li key={cardKey(card)}><span className="material-icon">{card.kind === 'topic' ? '◉' : '▤'}</span><div><strong>{card.title}</strong><small>{card.kind === 'topic' ? 'Тема' : 'Обращение'} · {card.id} · версия {card.version}</small></div><button className="icon-button" aria-label={'Убрать из отчёта: ' + card.title} onClick={() => onRemove(card)}>×</button></li>)}</ul>
          <button className="button-primary" disabled={!selected.length || !title.trim()} onClick={() => { void save(false) }}>{busy ? 'Сохраняем…' : 'Сохранить подборку в архив'}</button>
        </fieldset></section>
      <section className="surface archive-panel" aria-label="Архив отчётов"><div className="section-heading"><div><span className="eyebrow">СОХРАНЁННЫЕ СНИМКИ</span><h2>Архив отчётов</h2></div><button className="text-button" onClick={() => setRevision(n => n + 1)}>Обновить архив</button></div>
        <p className="subtle">Содержимое фиксируется при сохранении. Последующие решения и повторы анализа не переписывают старый отчёт. Автоматического удаления нет.</p>
        {archiveError && <p role="alert" className="workspace-error">{archiveError}</p>}
        {archive?.total === 0 && <p className="empty-caption">Пока нет сохранённых отчётов.</p>}
        <ul className="archive-list">{archive?.items.map(entry => <li key={entry.report_id} className={entry.kind === 'weekly' ? 'weekly-archive' : ''} onClick={event => { if (!(event.target instanceof Element && event.target.closest('button')) && !busy) void open(entry.report_id) }}><span className="archive-icon">▤</span><div><button className="archive-title" disabled={busy} onClick={() => { void open(entry.report_id) }}>{entry.title}</button><small>{formatTopicDate(entry.generated_at)} МСК · {entry.kind === 'weekly' ? 'Недельная сводка' : `Подборка · ${entry.selection_count} объектов`} · {entry.model_mode}</small></div><span className="archive-open-icon" aria-hidden="true">＋</span></li>)}</ul>
        {archive && archive.total > 20 && <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Предыдущие отчёты</button><button disabled={offset + 20 >= archive.total} onClick={() => setOffset(offset + 20)}>Следующие отчёты</button></div>}
      </section>
    </div><aside className="report-preview"><section className="surface"><span className="eyebrow">СОХРАНЁННЫЙ ОТЧЁТ</span><h2>{snapshot?.title ?? 'Предпросмотр и экспорт'}</h2>
      {!snapshot && <p>Сохраните подборку или откройте снимок из архива. Скачивание доступно после подтверждения сервера.</p>}
      {snapshot && <><p>{formatTopicDate(snapshot.generated_at)} МСК · {snapshot.model_mode}</p><Downloads key={snapshot.report_id} snapshot={snapshot} onView={() => showSnapshot()} onPrint={() => showSnapshot(true)} /><p className="subtle">{snapshot.limitations}</p></>}
      {reading && <p role="status">Загружаем снимок…</p>}
      {error && viewing === null && <p role="alert" className="workspace-error">{error}</p>}
      {message && <p role="status" className="workspace-success">{message}</p>}
    </section></aside></div>
    <WorkspaceDrawer className="report-viewer" open={viewing !== null} wide title="Просмотр отчёта" onClose={() => { request.current?.abort(); setReading(false); setViewing(null); setPrinting(false) }}>
      {reading && <p role="status">Загружаем сохранённый отчёт…</p>}
      {error && viewing !== null && <p role="alert" className="workspace-error">{error}</p>}
      {snapshot && viewing === snapshot.report_id && !reading && <><div className="report-viewer-actions"><button className="button-primary" onClick={() => showSnapshot(true)}>Печать отчёта</button><span>Для скачивания PDF используйте отдельную кнопку в разделе экспорта.</span></div><ReportDocument snapshot={snapshot} /></>}
    </WorkspaceDrawer>
  </>
}
