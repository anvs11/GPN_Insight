import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { ImportPreview, ImportResult } from '../api-types'
import { maxManifestBytes } from '../import-manifest'
import { readMediaManifest } from '../media-manifest'
import { SourceViewer } from './SourceViewer'
import { WorkRules } from './WorkRules'

type Snapshot = { raw: string; files: File[]; sourceIds: string[]; result: ImportPreview }

export function MediaImportPanel({ onImported }: { onImported?: () => void }) {
  const [manifest, setManifest] = useState<{ name: string; raw: string } | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [reading, setReading] = useState(false)
  const [busy, setBusy] = useState<'preview' | 'commit' | null>(null)
  const [preview, setPreview] = useState<Snapshot | null>(null)
  const [receipt, setReceipt] = useState<ImportResult | null>(null)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [journal, setJournal] = useState('')
  const [pendingIds, setPendingIds] = useState<string[]>([])
  const [confirmedIds, setConfirmedIds] = useState<string[]>([])
  const [sourceId, setSourceId] = useState<string | null>(null)
  const journalPending = useRef<AbortController | null>(null)
  const pending = useRef<AbortController | null>(null)
  const writing = useRef(false)
  const unknown = useRef(false)
  const manifestRevision = useRef(0)
  useEffect(() => () => { pending.current?.abort(); journalPending.current?.abort(); manifestRevision.current += 1 }, [])
  function invalidate() {
    pending.current?.abort(); pending.current = null
    setBusy(null); setPreview(null); setError('')
  }
  async function chooseManifest(file?: File) {
    if (writing.current || unknown.current) return
    const revision = ++manifestRevision.current
    invalidate(); setManifest(null); setReading(false)
    if (!file) return
    if (file.size > maxManifestBytes) { setError('JSON превышает 1 МиБ.'); return }
    setReading(true)
    try {
      const raw = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer())
      if (manifestRevision.current === revision) setManifest({ raw, name: file.name })
    } catch { if (manifestRevision.current === revision) setError('JSON не прочитан. Выберите UTF-8 без BOM.') }
    finally { if (manifestRevision.current === revision) setReading(false) }
  }
  async function check() {
    if (pending.current || writing.current || unknown.current || reading) return
    setError(''); setPreview(null)
    let parsed: ReturnType<typeof readMediaManifest>
    try {
      if (!manifest) throw new Error('Сначала выберите JSON manifest.')
      parsed = readMediaManifest(manifest.raw, files)
    } catch (failure) { setError(failure instanceof SyntaxError ? 'JSON не разобран. Исправьте файл.' : failure instanceof Error ? failure.message : 'Проверьте набор.'); return }
    const raw = manifest!.raw, chosen = [...files]
    const controller = new AbortController(); pending.current = controller; setBusy('preview')
    try {
      const result = await api.previewImport(raw, controller.signal, chosen)
      if (controller.signal.aborted) return
      if (result.can_commit && (result.dataset_id !== parsed.datasetId || result.counts.inserted + result.counts.updated + result.counts.unchanged !== parsed.sourceIds.length)) {
        throw new Error('Предпросмотр не соответствует набору. Подтверждение заблокировано.')
      }
      setPreview({ raw, files: chosen, sourceIds: parsed.sourceIds, result })
    } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Предпросмотр не получен.') }
    finally { if (pending.current === controller) { pending.current = null; setBusy(null) } }
  }
  async function commit() {
    if (!preview?.result.can_commit || pending.current || writing.current || unknown.current) return
    const snapshot = preview, controller = new AbortController()
    setPendingIds(snapshot.sourceIds); setJournal('')
    writing.current = true; pending.current = controller; setBusy('commit'); setError('')
    try {
      const result = await api.commitImport(snapshot.raw, controller.signal, snapshot.files)
      if (controller.signal.aborted) return
      if (result.dataset_id !== snapshot.result.dataset_id || result.counts.inserted + result.counts.updated + result.counts.unchanged !== snapshot.sourceIds.length) {
        throw new ApiError(200, { code: 'INVALID_RESPONSE', message: 'Квитанция не соответствует набору. Запись могла пройти; сверьте журнал и источники.' })
      }
      setReceipt(result); setConfirmedIds(snapshot.sourceIds); setPreview(null); onImported?.()
    } catch (failure) {
      if (controller.signal.aborted) return
      setPreview(null)
      setError(failure instanceof Error ? failure.message : 'Квитанция не получена.')
      if (!(failure instanceof ApiError) || ![400, 403, 404, 409, 413, 422].includes(failure.status)) {
        unknown.current = true; setUncertain(true)
      }
    } finally { writing.current = false; if (pending.current === controller) { pending.current = null; setBusy(null) } }
  }
  async function readJournal() {
    journalPending.current?.abort()
    const controller = new AbortController(); journalPending.current = controller
    try {
      const entries = await api.imports(controller.signal)
      if (!controller.signal.aborted) setJournal(entries.slice(-10).reverse().map(e => `${e.result.dataset_id} · ${e.result.imported_at} · ${e.registration_id}`).join('\n') || 'Завершённых регистраций нет.')
    } catch (failure) { if (!controller.signal.aborted) setJournal(failure instanceof Error ? failure.message : 'Журнал не получен.') }
    finally { if (journalPending.current === controller) journalPending.current = null }
  }
  return <section className="topic-list" aria-label="Импорт медиа">
    <h2 id="media-import-title">Импорт обращений с медиа</h2>
    <p className="import-introduction">Прикрепите описание набора и файлы. Предпросмотр проверяет их, а подтверждение сохраняет.</p>
    <ol className="import-steps" aria-label="Шаги медиаимпорта"><li className={preview || receipt ? 'done' : 'active'}>Выбрать файлы</li><li className={preview ? 'active' : ''}>Предпросмотр</li><li className={receipt ? 'done' : ''}>Подтвердить импорт</li><li>Обработать</li></ol>
    <WorkRules><p>Только synthetic=true. WAV/MP3 и PNG/JPEG. Загрузка файла и распознавание — разные этапы; replay работает только для известных входов.</p><p>В JSON: dataset_id, synthetic=true, tickets с text и assets. В assets.path — точные имена выбранных файлов, без каталогов.</p><p>JSON до 1 МиБ; до 30 файлов / 50 МиБ. Аудио до 10 МиБ и 60 секунд, изображение до 5 МиБ. Фактический состав и ограничения проверяет сервер.</p></WorkRules>
    <form onSubmit={e => { e.preventDefault(); void check() }}>
      <fieldset disabled={busy === 'commit' || uncertain}>
        <legend>Набор с вложениями</legend>
        <label>JSON manifest для медиа<input type="file" accept=".json,application/json" onChange={e => { void chooseManifest(e.target.files?.[0]) }} /></label>
        <label>Файлы WAV, MP3, PNG, JPEG<input type="file" multiple accept=".wav,.mp3,.png,.jpg,.jpeg" onChange={e => {
          if (writing.current || unknown.current) return
          invalidate(); setFiles(Array.from(e.target.files ?? []))
        }} /></label>
        <p className="step-caption">JSON связывает файлы с обращениями. Выберите его вместе с перечисленными в нём вложениями.</p>
        {manifest && <p>JSON: {manifest.name}. Исходный JSON отправляется без пересборки браузером.</p>}
        <ul>{files.map((file, i) => <li key={i}>{file.name} · {file.size} байт</li>)}</ul>
        <button disabled={busy !== null || reading}>{busy === 'preview' ? 'Проверяем медиа…' : 'Предпросмотр медиа'}</button>
      </fieldset>
    </form>
    {reading && <p role="status">Читаем JSON…</p>}
    {error && <p role="alert" className="topic-list-error">{error}</p>}
    {preview && <section aria-label="Предпросмотр медиа">
      <p>Набор: {preview.result.dataset_id}. Импорт ещё не выполнен.</p>
      <p>Новых: {preview.result.counts.inserted}; обновлений: {preview.result.counts.updated}; без изменений: {preview.result.counts.unchanged}; ошибок: {preview.result.counts.invalid}.</p>
      {preview.result.errors.map((item, i) => <p role="alert" key={i}>{item.code} · {item.message}</p>)}
      {preview.result.warnings.map((item, i) => <p key={i}>{item}</p>)}
      <p>Изменение JSON или выбора файлов отменяет разрешение. Commit повторно проверит тот же набор; частичного импорта нет.</p>
      <button disabled={!preview.result.can_commit || busy !== null} onClick={() => { void commit() }}>{busy === 'commit' ? 'Сохраняем медиа…' : 'Подтвердить импорт медиа'}</button>
    </section>}
    {uncertain && <section role="alert" className="topic-list-stale">
      <p>Результат записи неизвестен. Автоматической повторной отправки нет. Сверьте журнал и источники; одно совпадение dataset_id не доказывает совпадение файлов.</p>
      <button onClick={() => { void readJournal() }}>Проверить журнал медиаимпорта</button>
      <ul>{pendingIds.map(id => <li key={id}><button onClick={() => setSourceId(id)}>Сверить медиаисточник {id}</button></li>)}</ul>
      {journal && <pre className="weekly-text">{journal}</pre>}
      <button onClick={() => { unknown.current = false; setUncertain(false); setPreview(null); setError('') }}>Результат проверен — вернуться к проверке медиа</button>
    </section>}
    {receipt && <section aria-label="Подтверждённый медиаимпорт">
      <h3>Последний подтверждённый медиаимпорт</h3>
      <p>Backend подтвердил импорт {receipt.dataset_id}: новых {receipt.counts.inserted}, обновлений {receipt.counts.updated}, без изменений {receipt.counts.unchanged}.</p>
      <p>Файлы сохранены, распознавание ещё не запускалось. Повтор неизменного набора не создаёт дублей.</p>
      <div className="import-next-step"><strong>4. Перейдите к обработке</strong><p>Она распознаёт зарегистрированные материалы и выделяет потребности. Replay требует точных сохранённых ответов для этого набора.</p><a className="button-primary" href="#processing-title">Перейти к запуску обработки</a></div>
      <ul>{confirmedIds.map(id => <li key={id}><button onClick={() => setSourceId(id)}>Сверить медиаисточник {id}</button></li>)}</ul>
    </section>}
    {sourceId && <SourceViewer key={sourceId} sourceId={sourceId} onClose={() => setSourceId(null)} />}
  </section>
}
