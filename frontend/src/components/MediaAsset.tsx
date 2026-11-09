import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { MaterialInput, SourceDetail } from '../api-types'

export function MediaAsset({ source, asset }: { source: SourceDetail; asset: MaterialInput }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  const objectUrl = useRef('')
  useEffect(() => () => { pending.current?.abort(); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current) }, [])
  async function load() {
    if (pending.current) return
    const controller = new AbortController(); pending.current = controller
    setBusy(true); setError(''); setUrl('')
    if (objectUrl.current) { URL.revokeObjectURL(objectUrl.current); objectUrl.current = '' }
    try {
      const value = await api.media(source, asset, controller.signal)
      if (!controller.signal.aborted) { objectUrl.current = URL.createObjectURL(value); setUrl(objectUrl.current) }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof ApiError && failure.status === 403
        ? 'Доступ к этому оригиналу закрыт. Файл не показан.'
        : failure instanceof Error ? failure.message : 'Файл не получен. Повторите загрузку.')
    } finally { if (pending.current === controller) { pending.current = null; setBusy(false) } }
  }
  if (!source.synthetic || source.original_access === 'restricted') return <p>Оригинал {asset.asset_id} закрыт до согласования доступа.</p>
  return <div className="source-media">
    <button disabled={busy} onClick={() => { void load() }}>{busy ? 'Загружаем файл…' : `Открыть медиа ${asset.asset_id}`}</button>
    <p>Синтетический оригинал · версия источника {source.source_version}. Показ файла не подтверждает качество распознавания.</p>
    {error && <p role="alert" className="topic-list-error">{error}</p>}
    {url && (asset.kind === 'audio' ? <audio controls preload="metadata" src={url} aria-label={`Аудио ${asset.asset_id}`} onError={() => setError('Браузер не воспроизвёл аудио. Это ошибка просмотра, не результат ASR.')} />
      : <img src={url} alt={`Синтетическое вложение ${asset.asset_id}`} onError={() => setError('Браузер не показал изображение. Повторите чтение файла.')} />)}
  </div>
}
