import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { ImportCounts, ImportLogEntry, ImportPreview, ImportResult } from '../api-types'
import { maxManifestBytes, readTextManifest, textManifest } from '../import-manifest'
import { SourceViewer } from './SourceViewer'
import { WorkRules } from './WorkRules'
import './ImportPanel.css'

function failureOf(failure: unknown) {
  return failure instanceof ApiError ? failure : new ApiError(0, {
    code: 'REQUEST_FAILED', message: 'Не удалось выполнить запрос. Проверьте Backend; введённые данные сохранены.',
  })
}
function Counts({ counts }: { counts: ImportCounts }) {
  return <dl className="import-counts">
    <div><dt>Новых</dt><dd>{counts.inserted}</dd></div>
    <div><dt>Обновлённых</dt><dd>{counts.updated}</dd></div>
    <div><dt>Без изменений</dt><dd>{counts.unchanged}</dd></div>
    <div><dt>Ошибок</dt><dd>{counts.invalid}</dd></div>
  </dl>
}
type Preview = { raw: string; result: ImportPreview; tickets: ReturnType<typeof readTextManifest>['tickets'] }

export function ImportPanel({ onImported }: { onImported?: () => void } = {}) {
  const [mode, setMode] = useState<'text' | 'file'>('text')
  const [datasetId, setDatasetId] = useState('synthetic-frontend-import')
  const [ticket, setTicket] = useState({ source_id: '', created_at: '', text: '', requester_id: '' })
  const [file, setFile] = useState<{ name: string; raw: string } | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState<'preview' | 'commit' | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [localError, setLocalError] = useState('')
  const [receipt, setReceipt] = useState<ImportResult | null>(null)
  const [importedIds, setImportedIds] = useState<string[]>([])
  const [pendingIds, setPendingIds] = useState<string[]>([])
  const [journalOutdated, setJournalOutdated] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [journal, setJournal] = useState<ImportLogEntry[] | null>(null)
  const [journalBusy, setJournalBusy] = useState(false)
  const [journalError, setJournalError] = useState<ApiError | null>(null)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const journalRef = useRef<AbortController | null>(null)
  const fileRevision = useRef(0)
  const uncertainRef = useRef(false)

  useEffect(() => () => {
    requestRef.current?.abort(); journalRef.current?.abort(); fileRevision.current += 1
  }, [])

  function invalidate() {
    requestRef.current?.abort()
    requestRef.current = null
    setBusy(null); setPreview(null); setError(null); setLocalError('')
  }

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0]
    const revision = ++fileRevision.current
    invalidate(); setFile(null); setFileLoading(false)
    if (!chosen) return
    if (chosen.size > maxManifestBytes) { setLocalError('JSON превышает 1 МиБ. Разделите набор.'); return }
    setFileLoading(true)
    try {
      let raw: string
      try { raw = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await chosen.arrayBuffer()) }
      catch { throw new Error('Файл должен быть корректным UTF-8 JSON без BOM. Пересохраните его и повторите выбор.') }
      if (fileRevision.current !== revision) return
      readTextManifest(raw)
      setFile({ name: chosen.name, raw })
    } catch (failure) {
      if (fileRevision.current === revision) setLocalError(failure instanceof Error ? failure.message : 'Файл не прочитан. Выберите UTF-8 JSON.')
    } finally {
      if (fileRevision.current === revision) setFileLoading(false)
    }
  }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestRef.current || uncertainRef.current || fileLoading) return
    setPreview(null); setError(null); setLocalError('')
    let raw: string
    let parsed: ReturnType<typeof readTextManifest>
    try {
      if (mode === 'file' && !file) throw new Error('Сначала выберите текстовый JSON-набор.')
      raw = mode === 'file' ? (file?.raw ?? '') : textManifest(datasetId, ticket)
      parsed = readTextManifest(raw)
    } catch (failure) { setLocalError(failure instanceof Error ? failure.message : 'Проверьте данные.'); return }
    const controller = new AbortController()
    requestRef.current = controller; setBusy('preview')
    try {
      const result = await api.previewImport(raw, controller.signal)
      if (controller.signal.aborted) return
      if (result.can_commit && (result.dataset_id !== parsed.datasetId ||
        result.counts.inserted + result.counts.updated + result.counts.unchanged !== parsed.tickets.length)) {
        throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Предпросмотр не соответствует выбранному набору. Импорт заблокирован.' })
      }
      setPreview({ raw, result, tickets: parsed.tickets })
    } catch (failure) { if (!controller.signal.aborted) setError(failureOf(failure)) } finally {
      if (requestRef.current === controller) { requestRef.current = null; setBusy(null) }
    }
  }

  async function commit() {
    if (requestRef.current || uncertainRef.current || !preview?.result.can_commit || preview.tickets.length === 0) return
    const snapshot = preview
    const controller = new AbortController()
    requestRef.current = controller; setBusy('commit'); setError(null)
    setPendingIds(snapshot.tickets.map(item => item.source_id)); setJournalOutdated(true)
    // Повторное нажатие блокируется ref до следующего рендера, а не только disabled.
    try {
      const result = await api.commitImport(snapshot.raw, controller.signal)
      if (controller.signal.aborted) return
      if (result.dataset_id !== snapshot.result.dataset_id ||
        result.counts.inserted + result.counts.updated + result.counts.unchanged !== snapshot.tickets.length) {
        throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Квитанция не соответствует набору. Проверьте журнал; повторная запись заблокирована.' })
      }
      setReceipt(result)
      onImported?.()
      setImportedIds(snapshot.tickets.map(item => item.source_id))
      setPreview(null)
      void readJournal()
      // Даже unchanged подтверждается только ImportResult после серверного commit.
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = failureOf(failure)
      setError(problem)
      setPreview(null)
      // 503 / потеря ответа / неизвестный 200 могут случиться после записи.
      if (![400, 403, 404, 409, 413, 422].includes(problem.status)) {
        uncertainRef.current = true; setUncertain(true)
      }
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setBusy(null) }
    }
  }

  async function readJournal() {
    journalRef.current?.abort()
    const controller = new AbortController()
    journalRef.current = controller; setJournalBusy(true); setJournalError(null)
    try {
      const result = await api.imports(controller.signal)
      if (!controller.signal.aborted) { setJournal(result); setJournalOutdated(false) }
    } catch (failure) { if (!controller.signal.aborted) setJournalError(failureOf(failure)) } finally {
      if (journalRef.current === controller) { journalRef.current = null; setJournalBusy(false) }
    }
  }

  const locked = busy === 'commit' || uncertain
  return <section className="topic-list import-panel" aria-labelledby="import-title">
    <h2 id="import-title">Импорт текстовых обращений</h2>
    <p className="import-introduction">Демонстрационные данные: сначала проверка, затем подтверждение импорта. Анализ запускается отдельно.</p>
    <WorkRules><p>Только синтетические демонстрационные данные. Для файлов с вложениями выберите вкладку «Медиа».</p><p>Предпросмотр ничего не сохраняет и не вызывает модель. Правка сбрасывает разрешение на импорт.</p><p>Черновик сохраняется при переходах между разделами до перезагрузки страницы. Сохраните исходный файл у себя.</p></WorkRules>
    <form aria-label="Текстовый импорт" onSubmit={event => { void check(event) }}>
      <fieldset disabled={locked}>
        <legend>1. Подготовьте данные</legend>
        <label>Способ ввода<select value={mode} onChange={event => {
          invalidate(); fileRevision.current += 1; setFileLoading(false); setMode(event.target.value as typeof mode)
        }}><option value="text">Одно текстовое обращение</option><option value="file">JSON-набор без вложений</option></select></label>
        {mode === 'text' ? <div className="import-fields">
          <label>Идентификатор набора<input required maxLength={64} value={datasetId} onChange={event => { invalidate(); setDatasetId(event.target.value) }} /></label>
          <label>ID обращения<input required maxLength={128} value={ticket.source_id} onChange={event => { invalidate(); setTicket({ ...ticket, source_id: event.target.value }) }} /></label>
          <label>Дата с часовым поясом<input required placeholder="2026-10-09T12:00:00+03:00" value={ticket.created_at}
            onChange={event => { invalidate(); setTicket({ ...ticket, created_at: event.target.value }) }} /></label>
          <label>ID заявителя (необязательно)<input maxLength={128} value={ticket.requester_id}
            onChange={event => { invalidate(); setTicket({ ...ticket, requester_id: event.target.value }) }} /></label>
          <label className="import-wide">Текст обращения<textarea required rows={5} value={ticket.text}
            onChange={event => { invalidate(); setTicket({ ...ticket, text: event.target.value }) }} /></label>
        </div> : <><label>Текстовый JSON-файл (UTF-8, до 1 МиБ)<input type="file" accept=".json,application/json" onChange={event => { void chooseFile(event) }} /></label>
          <p>Формат: dataset_id, synthetic=true, tickets (до 100). У обращения: source_id, product_id=webneft, created_at с часовым поясом, text; assets — пустой массив.</p>
          {file && <p>Прочитан файл: {file.name}. Он отправляется без исправления содержимого браузером.</p>}
        </>}
        <button type="submit" disabled={busy !== null || fileLoading}>{busy === 'preview' ? 'Проверяем набор…' : 'Предпросмотр'}</button>
      </fieldset>
    </form>
    {fileLoading && <p role="status">Читаем выбранный файл…</p>}
    {busy && <p role="status">{busy === 'preview' ? 'Backend проверяет структуру и повторы…' : 'Сохраняем обращения. Дождитесь квитанции Backend…'}</p>}
    {localError && <p role="alert" className="topic-list-error">{localError}</p>}
    {error && <div role="alert" className="topic-list-error"><p>{error.message}</p>
      <small>{error.status > 0 ? 'HTTP ' + error.status + ' · ' : ''}{error.code}</small>
      {error.status === 409 && <p>Импорт или обработка уже выполняется. Дождитесь завершения и повторите предпросмотр.</p>}
      {error.status === 413 && <p>Разделите набор: JSON должен быть не больше 1 МиБ, не больше 100 обращений.</p>}
      {error.status === 422 && <p>Исправьте данные и снова выполните предпросмотр. Введённые значения сохранены.</p>}
    </div>}
    {uncertain && <div role="alert" className="topic-list-stale">
      <p><strong>Результат импорта не подтверждён. Запись могла пройти.</strong> Автоматической повторной отправки нет.</p>
      <p>Проверьте журнал. Совпадение dataset_id само по себе не доказывает, что сохранён именно этот текст.</p>
      <button type="button" onClick={() => { void readJournal() }}>Проверить журнал без повторной записи</button>
      <ul>{pendingIds.map(id => <li key={id}><button type="button" onClick={() => setSourceId(id)}>Сверить источник {id}</button></li>)}</ul>
      <p>Повтор того же набора обычно вернёт «Без изменений». Если другой пользователь уже обновил те же ID, старый текст может заменить его версию. Сначала сверяйте источники.</p>
      <button type="button" onClick={() => {
        uncertainRef.current = false; setUncertain(false); setError(null); setPreview(null)
      }}>Результат проверен — вернуться к предпросмотру</button>
    </div>}
    {preview && <section aria-label="Предпросмотр набора" aria-busy={busy === 'commit'}>
      <h3>2. Предварительная проверка Backend</h3>
      <p>{preview.result.can_commit ? 'Набор прошёл проверку. Импорт ещё не выполнен.' : 'Набор не прошёл проверку. Подтверждение недоступно.'}</p>
      <p>Набор: {preview.result.dataset_id ?? 'не определён'}</p><Counts counts={preview.result.counts} />
      {preview.result.errors.map((issue, index) => <div role="alert" key={index} className="topic-list-error">
        <p>{issue.message}</p><small>{issue.code}{issue.field && ' · ' + issue.field}{issue.source_id && ' · ' + issue.source_id}{issue.asset_id && ' · ' + issue.asset_id}</small>
      </div>)}
      {preview.result.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      <p>Счётчики предварительные и могут измениться. Подтверждение повторно проверяет весь набор; частичного импорта нет.</p>
      {preview.tickets.length === 0 ? <p>В наборе нет обращений. Импортировать нечего.</p> : <details><summary>Содержимое выбранного синтетического набора ({preview.tickets.length})</summary>
        <p>Это ваш исходный ввод, а не результат нормализации или ИИ.</p>
        <ul>{preview.tickets.map((item, index) => <li key={index}><strong>{item.source_id}</strong> · {item.created_at}<p className="topic-text">{item.text}</p></li>)}</ul>
      </details>}
      <button type="button" disabled={!preview.result.can_commit || preview.tickets.length === 0 || busy !== null}
        onClick={() => { void commit() }}>{busy === 'commit' ? 'Импортируем…' : 'Подтвердить импорт'}</button>
    </section>}
    {receipt && <section aria-label="Подтверждённый импорт">
      <h3>Последний подтверждённый импорт</h3>
      <p>Backend подтвердил сохранение набора {receipt.dataset_id} в {new Date(receipt.imported_at).toLocaleString('ru-RU')}.</p>
      <Counts counts={receipt.counts} />
      <p><strong>Это подтверждение импорта, а не успешного ИИ-анализа.</strong> Новые и изменённые обращения ожидают отдельного запуска.</p>
      <p>Повтор неизменного содержимого не создаёт новые версии обращений. Изменение текста при том же ID создаёт новую версию; одно совпадение текста у разных ID не означает дубль.</p>
      <ul aria-label="Импортированные обращения">{importedIds.map(id => <li key={id}>
        <button type="button" onClick={() => setSourceId(id)}>Проверить источник {id}</button>
      </li>)}</ul>
    </section>}
    {receipt && <div className="import-next-step"><strong>Далее — анализ импортированных обращений</strong><p>Откройте отдельный раздел обработки. После завершения результаты появятся в мониторинге.</p><a className="button-primary" href="#processing-title">Перейти к запуску обработки</a><a className="button-secondary" href="#topic-list-title">Перейти к темам и карточкам</a></div>}
    <button type="button" onClick={() => { void readJournal() }}>{journalBusy ? 'Обновить запрос журнала' : 'Загрузить журнал импортов'}</button>
    {journalBusy && <p role="status">Загружаем журнал импортов…</p>}
    {journalError && <div role="alert" className="topic-list-error"><p>{journalError.message}</p><small>{journalError.code}</small></div>}
    {journal && (journalBusy || journalError || journalOutdated) && <p className="topic-list-stale">Показан ранее полученный журнал; актуальность не подтверждена.</p>}
    {journal && <section aria-label="Журнал импортов">
      <h3>Журнал завершённых регистраций</h3>
      <p>Это квитанции Backend, а не результаты анализа. Записей: {journal.length}. Показаны последние 20 в обратном порядке регистрации.</p>
      {journal.length === 0 && <p>Завершённых импортов нет.</p>}
      <ol>{journal.slice(-20).reverse().map(entry => <li key={entry.registration_id}>
        <p>{entry.result.dataset_id} · {entry.synthetic ? 'Синтетические данные' : 'Не отмечено как синтетическое'}</p>
        <p>{new Date(entry.result.imported_at).toLocaleString('ru-RU')} · ID регистрации: {entry.registration_id}</p><Counts counts={entry.result.counts} />
      </li>)}</ol>
    </section>}
    {sourceId && <SourceViewer key={sourceId} sourceId={sourceId} onClose={() => setSourceId(null)} />}
  </section>
}
