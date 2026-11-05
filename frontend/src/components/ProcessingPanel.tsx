import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { Health, Origin, Run } from '../api-types'
import './ProcessingPanel.css'

const draftKey = 'gpn-insight.processing-draft.v1'
const lastRunKey = 'gpn-insight.last-run.v1'

function readStored(key: string): string {
  try { return sessionStorage.getItem(key) ?? '' } catch { return '' }
}

function store(key: string, value: string) {
  try { sessionStorage.setItem(key, value) } catch { /* Форма продолжает работать без хранилища браузера. */ }
}

function readDraft(): { from: string; to: string } {
  try {
    const draft: unknown = JSON.parse(readStored(draftKey))
    if (typeof draft === 'object' && draft !== null && 'from' in draft && 'to' in draft &&
      typeof draft.from === 'string' && typeof draft.to === 'string') return { from: draft.from, to: draft.to }
  } catch { /* Повреждённый черновик не мешает открыть интерфейс. */ }
  return { from: '', to: '' }
}

function errorOf(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, {
    code: 'REQUEST_FAILED', message: 'Запрос не выполнен. Проверьте соединение и повторите действие.',
  })
}

const stateLabels: Record<Run['state'], string> = {
  queued: 'В очереди', running: 'Обрабатывается', completed: 'Завершён',
  completed_with_errors: 'Завершён с ошибками', failed: 'Не завершён из-за ошибки', interrupted: 'Прерван',
}
const stageLabels: Record<string, string> = {
  queued: 'Ожидание', extract: 'Подготовка и извлечение потребностей',
  normalization: 'Подготовка материалов', assignment: 'Сопоставление тем',
  finished: 'Обработка закончена', failed: 'Ошибка', worker: 'Обработчик запуска',
}
const integrationLabels: Record<string, string> = {
  NORMALIZER_NOT_CONNECTED: 'Подготовка материалов участника №1 ещё не подключена.',
  ANALYSIS_NOT_CONNECTED: 'Анализ участника №2 ещё не подключён.',
}

function originsText(origins: Origin[]) {
  return origins.length ? origins.map(origin => origin === 'authored'
    ? 'authored — подготовлено вручную' : 'model — ответ модели').join('; ') : 'Происхождение ответов пока не указано'
}

function ErrorNotice({ error }: { error: ApiError }) {
  return <div className="processing-error" role="alert">
    <strong>{error.code === 'INTEGRATION_NOT_READY' ? 'Обработка пока недоступна: интеграция не завершена.' : 'Запрос не выполнен.'}</strong>
    <p>{error.message}</p>
    <small>{error.status > 0 ? `HTTP ${error.status} · ` : ''}{error.code}</small>
  </div>
}

export function ProcessingPanel() {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<ApiError | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)
  const [healthRefresh, setHealthRefresh] = useState(0)
  const [healthTime, setHealthTime] = useState('')
  const healthController = useRef<AbortController | null>(null)
  const [draft, setDraft] = useState(readDraft)
  const [starting, setStarting] = useState(false)
  const startController = useRef<AbortController | null>(null)
  const [startError, setStartError] = useState<ApiError | null>(null)
  const [lookup, setLookup] = useState(() => readStored(lastRunKey))
  const [runId, setRunId] = useState(() => readStored(lastRunKey))
  const [run, setRun] = useState<Run | null>(null)
  const [runError, setRunError] = useState<ApiError | null>(null)
  const [runLoading, setRunLoading] = useState(() => Boolean(readStored(lastRunKey)))
  const [runRefresh, setRunRefresh] = useState(0)

  function refreshHealth() {
    healthController.current?.abort()
    setHealthLoading(true)
    setHealthError(null)
    setHealthRefresh(value => value + 1)
  }

  useEffect(() => {
    const controller = new AbortController()
    healthController.current = controller
    async function loadHealth() {
      try {
        const result = await api.health(controller.signal)
        if (!controller.signal.aborted) {
          setHealth(result)
          setHealthTime(new Date().toLocaleTimeString('ru-RU'))
        }
      } catch (error) {
        if (!controller.signal.aborted) { setHealth(null); setHealthError(errorOf(error)) }
      } finally {
        if (!controller.signal.aborted) setHealthLoading(false)
      }
    }
    void loadHealth()
    return () => { controller.abort() }
  }, [healthRefresh])

  useEffect(() => () => { startController.current?.abort() }, [])

  useEffect(() => { store(draftKey, JSON.stringify(draft)) }, [draft])

  useEffect(() => {
    if (!runId) return
    const controller = new AbortController()
    const polling: { timer?: number } = {}
    async function update() {
      if (controller.signal.aborted) return
      try {
        const result = await api.run(runId, controller.signal)
        if (controller.signal.aborted) return
        setRun(result)
        setRunError(null)
        store(lastRunKey, result.run_id)
        if (result.state === 'queued' || result.state === 'running') {
          polling.timer = window.setTimeout(() => {
            setRunLoading(true)
            void update()
          }, 2000)
        }
      } catch (error) {
        if (!controller.signal.aborted) setRunError(errorOf(error))
      } finally {
        if (!controller.signal.aborted) setRunLoading(false)
      }
    }
    void update()
    return () => { controller.abort(); window.clearTimeout(polling.timer) }
  }, [runId, runRefresh])

  const invalidPeriod = Boolean(draft.from && draft.to && draft.from > draft.to)
  const active = run?.state === 'queued' || run?.state === 'running'

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!health?.pipeline_ready || healthLoading || startController.current || invalidPeriod || active) return
    const controller = new AbortController()
    startController.current = controller
    setStarting(true)
    setStartError(null)
    try {
      const result = await api.startRun({ product_id: 'webneft',
        date_from: draft.from || null, date_to: draft.to || null }, controller.signal)
      if (controller.signal.aborted) return
      setRun(result)
      setRunLoading(true)
      setRunError(null)
      setRunId(result.run_id)
      setLookup(result.run_id)
      store(lastRunKey, result.run_id)
      refreshHealth()
    } catch (error) {
      if (!controller.signal.aborted) {
        setStartError(errorOf(error))
        refreshHealth()
      }
    } finally {
      startController.current = null
      if (!controller.signal.aborted) setStarting(false)
    }
  }

  function findRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const id = lookup.trim()
    if (!id || runLoading) return
    refreshRun(id)
  }

  function refreshRun(id: string) {
    setRunLoading(true)
    setRunError(null)
    setRunId(id)
    setRunRefresh(value => value + 1)
  }

  return <section className="processing-panel" aria-labelledby="processing-title">
    <header className="processing-heading">
      <div><p className="processing-eyebrow">Вебнефть · обработка обращений</p><h2 id="processing-title">Состояние обработки</h2></div>
      <button type="button" onClick={refreshHealth} disabled={healthLoading}>
        {healthLoading ? 'Проверяем сервер…' : 'Обновить состояние'}
      </button>
    </header>

    {healthError && <ErrorNotice error={healthError} />}
    {!health && !healthError && <p role="status">Проверяем доступность сервера…</p>}
    {health && <>
      <div className="processing-facts">
        <div><span>Сервер</span><strong>Доступен</strong><small>SQLite отвечает · проверено в {healthTime}</small></div>
        <div><span>Режим сервера</span><strong>{health.model_mode}</strong><small>{health.model_mode === 'replay' ? 'Только точные сохранённые ответы' : 'Выбран режим внутренних моделей; готовность — ниже'}</small></div>
        <div><span>Обработчик</span><strong>{health.processing_state === 'busy' ? 'Занят' : health.processing_state === 'idle' ? 'Свободен' : 'Не подключён'}</strong><small>Это состояние отдельно от готовности интеграции</small></div>
      </div>
      {!health.pipeline_ready ? <div className="processing-warning" role="status">
        <strong>Обработка пока недоступна: интеграция не завершена.</strong>
        <ul>{health.integration_errors.map(code => <li key={code}>{integrationLabels[code] ?? 'Компонент ещё не готов.'} <small>{code}</small></li>)}</ul>
        <p>Сервер работает, но запуск анализа станет доступен после подключения компонентов команды.</p>
      </div> : <p className="processing-ready">Компоненты обработки подключены. Доступность моделей указана ниже.</p>}
      <details className="processing-models"><summary>Модели и сохранённые ответы</summary>
        <p>Записей replay: {health.replay_record_count}. {originsText(health.replay_origins)}.</p>
        <p>Режим задаётся на сервере. Ошибка не переключает live и replay.</p>
        {Object.entries(health.available_models).map(([kind, model]) => <div key={kind}>
          <strong>{kind === 'text' ? 'Текст' : kind === 'asr' ? 'Речь' : 'Изображения'}</strong>
          <p>{model.message}</p><small>{model.code} · {model.checked_at ? `Проверено: ${model.checked_at}` : 'Живая модель не проверена'}</small>
        </div>)}
      </details>
    </>}

    <form onSubmit={event => { void start(event) }} className="processing-form">
      <h3>Запустить обработку</h3>
      <p>Период создания обращений по Москве, включая обе даты. Пустые поля — весь доступный период. Загрузка новых обращений через браузер ещё не подключена.</p>
      <div className="processing-fields">
        <label>С даты<input type="date" value={draft.from} onChange={event => setDraft({ ...draft, from: event.target.value })} /></label>
        <label>По дату<input type="date" value={draft.to} onChange={event => setDraft({ ...draft, to: event.target.value })} /></label>
        <button disabled={!health?.pipeline_ready || healthLoading || starting || invalidPeriod || active}>
          {starting ? 'Отправляем запрос…' : 'Запустить обработку'}
        </button>
      </div>
      {invalidPeriod && <p role="alert">Начало периода должно быть не позже окончания.</p>}
      {active && <p>Запуск уже выполняется. Его состояние обновляется ниже.</p>}
      {startError && <ErrorNotice error={startError} />}
    </form>

    <form onSubmit={findRun} className="processing-form">
      <h3>Проверить сохранённый запуск</h3>
      <p>Укажите ID, полученный от сервера. API списка запусков пока нет. Последний открытый ID и период сохраняются в этой вкладке.</p>
      <div className="processing-fields"><label className="processing-wide">ID запуска
        <input type="text" value={lookup} maxLength={128} onChange={event => setLookup(event.target.value)} autoComplete="off" />
      </label><button disabled={!lookup.trim() || runLoading}>Показать запуск</button></div>
    </form>
    {runLoading && <p role="status">Получаем состояние запуска…</p>}
    {runError && <><ErrorNotice error={runError} />{run && <p>Ниже последние полученные данные запуска {run.run_id}; обновление не подтверждено.</p>}</>}
    {run && <section className="processing-run" aria-label="Сохранённый запуск">
      <header className="processing-heading"><h3>{stateLabels[run.state]}</h3><button type="button" disabled={runLoading} onClick={() => refreshRun(run.run_id)}>Обновить запуск</button></header>
      <p>ID: <code>{run.run_id}</code> · режим: <strong>{run.mode}</strong></p>
      <p>{originsText(run.origins)}.</p>
      <p>Этап: {stageLabels[run.stage] ?? run.stage}</p>
      <dl className="processing-counters">
        <div><dt>Всего материалов</dt><dd>{run.total_materials}</dd></div>
        <div><dt>Подготовлено успешно</dt><dd>{run.successful_materials}</dd></div>
        <div><dt>Ошибки материалов</dt><dd>{run.failed_materials}</dd></div>
        <div><dt>Покрытие подготовки</dt><dd>{run.coverage === null ? 'Нет данных' : `${Math.round(run.coverage * 100)}%`}</dd></div>
        <div><dt>Завершено шагов</dt><dd>{run.completed_steps}</dd></div>
        <div><dt>Повторно использовано</dt><dd>{run.reused_steps}</dd></div>
      </dl>
      <p>Покрытие относится к подготовке материалов. Даже при 100% анализ может содержать ошибки.</p>
      {run.state === 'completed' && run.total_materials === 0 && <p>Обращений за период нет.</p>}
      {run.state === 'interrupted' && <p>Сервер прервал запуск. Новый запуск с тем же периодом сможет использовать успешные этапы; прежний ID не возобновляется.</p>}
      {run.failure_code && <div role="alert" className="processing-error"><strong>{run.failure_code}</strong><p>{run.failure_message}</p></div>}
      {run.errors.length > 0 && <div className="processing-error"><h4>Ошибки этапов: {run.errors.length}</h4><ul>{run.errors.map((error, index) => <li key={`${error.source_id}-${index}`}>
        <strong>{error.source_id}</strong>{error.asset_id && ` · материал ${error.asset_id}`} · {stageLabels[error.stage] ?? error.stage}<p>{error.message}</p><small>{error.code}</small>
      </li>)}</ul></div>}
      <p><small>Создан: {new Date(run.created_at).toLocaleString('ru-RU')}{run.finished_at && ` · Завершён: ${new Date(run.finished_at).toLocaleString('ru-RU')}`}</small></p>
    </section>}
  </section>
}
