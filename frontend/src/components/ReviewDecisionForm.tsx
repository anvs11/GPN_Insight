import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { ResolveReviewRequest, ReviewItem } from '../api-types'
import { topicStatusLabels } from '../topic-labels'
import { contextKey, historyPreserved, reviewReadConsistent, reviewRequestHash, reviewVersions, verifyReview } from '../review-verification'
import type { ReviewAttempt, ReviewContext } from '../review-verification'
import { sameValue } from '../passport-verification'
import './ReviewDecisionForm.css'

function asError(failure: unknown) {
  return failure instanceof ApiError ? failure : new ApiError(0, {
    code: 'REQUEST_FAILED', message: 'Решение не подтверждено. Проверьте соединение и повторите чтение без новой записи.',
  })
}
async function readContext(id: string, signal: AbortSignal): Promise<ReviewContext> {
  const [review, history, topics] = await Promise.all([
    api.review(id, signal), api.reviewHistory(id, signal), api.topicCatalog(signal),
  ])
  if (!reviewReadConsistent(review, history)) throw new ApiError(200, {
    code: 'REVIEW_READ_CONFLICT', message: 'Review изменился во время чтения истории. Подтверждение результата не получено; повторите только чтение без новой записи.',
  })
  return { review, history, topics }
}

export function ReviewDecisionForm({ item, stale, onRead, onBusyChange }: {
  item: ReviewItem; stale: boolean; onRead: (item: ReviewItem) => void;
  onBusyChange: (id: string, busy: boolean) => boolean
}) {
  const [base, setBase] = useState<ReviewContext | null>(null)
  const [latest, setLatest] = useState<ReviewContext | null>(null)
  const [action, setAction] = useState<ResolveReviewRequest['action']>('attach')
  const [targetId, setTargetId] = useState('')
  const [author, setAuthor] = useState('')
  const [reason, setReason] = useState('')
  const [phase, setPhase] = useState<'idle' | 'loading' | 'sending' | 'checking'>('loading')
  const [error, setError] = useState<ApiError | null>(null)
  const [readError, setReadError] = useState<ApiError | null>(null)
  const [pending, setPending] = useState<ReviewAttempt | null>(null)
  const [checkedHash, setCheckedHash] = useState<string | null>(null)
  const [needsAccept, setNeedsAccept] = useState(false)
  const [confirmation, setConfirmation] = useState<{ kind: 'confirmed' | 'changed'; request: ResolveReviewRequest } | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const ownsGate = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    controllerRef.current = controller
    void readContext(item.review_id, controller.signal).then(result => {
      if (!controller.signal.aborted) { setBase(result); setLatest(result); onRead(result.review) }
    }).catch(failure => { if (!controller.signal.aborted) setReadError(asError(failure)) }).finally(() => {
      if (controllerRef.current === controller) controllerRef.current = null
      if (!controller.signal.aborted) setPhase('idle')
    })
    return () => { controller.abort() }
  }, [item.review_id, onRead])
  useEffect(() => () => {
    controllerRef.current?.abort()
    if (ownsGate.current) onBusyChange(item.review_id, false)
  }, [item.review_id, onBusyChange])

  const busy = phase !== 'idle'
  const closed = base?.review.status !== 'open' || base?.review.requires_recheck === true
  const propChanged = Boolean(base && (item.version !== base.review.version || item.requires_recheck !== base.review.requires_recheck))
  const selected = base?.topics.find(card => card.topic.topic_id === targetId)
  const versions = base ? reviewVersions(base, action, targetId) : {}
  const blocked = busy || !base || closed || stale || propChanged || needsAccept || pending !== null ||
    (action === 'attach' && !selected)
  const canAccept = !busy && !stale && !pending && latest?.review.status === 'open' && !latest.review.requires_recheck && needsAccept
  const canRepeat = !busy && !stale && pending && checkedHash === pending.hash && !pending.receipt && latest?.review.status === 'open' &&
    !latest.review.requires_recheck && latest.review.version === pending.request.version &&
    sameValue(pending.request.topic_versions, reviewVersions(latest, pending.request.action,
      pending.request.action === 'attach' ? pending.request.topic_id : ''))

  function begin(next: 'loading' | 'sending' | 'checking') {
    if (controllerRef.current || !onBusyChange(item.review_id, true)) return null
    const controller = new AbortController()
    controllerRef.current = controller; ownsGate.current = true
    setPhase(next); setConfirmation(null); setReadError(null); setCheckedHash(null)
    return controller
  }
  function finish(controller: AbortController) {
    if (controllerRef.current === controller) controllerRef.current = null
    if (ownsGate.current) { ownsGate.current = false; onBusyChange(item.review_id, false) }
    if (!controller.signal.aborted) setPhase('idle')
  }
  async function readLatest(controller: AbortController, attempt: ReviewAttempt | null) {
    setPhase('checking'); setReadError(null)
    try {
      const next = await readContext(item.review_id, controller.signal)
      if (controller.signal.aborted) return
      if (base && !historyPreserved(base.history, next.history)) throw new ApiError(200, {
        code: 'REVIEW_HISTORY_NOT_CONFIRMED', message: 'Прежняя история Review не подтверждена. Новая запись заблокирована.',
      })
      setLatest(next); setCheckedHash(attempt?.hash ?? null)
      if (!attempt) {
        if (!base) setBase(next)
        setNeedsAccept(previous => Boolean(base && contextKey(base) !== contextKey(next)) || previous)
        onRead(next.review)
        return
      }
      const result = verifyReview(attempt, next)
      if (result === 'unconfirmed') {
        if (next.review.status !== 'open' && next.review.resolution?.request_hash !== attempt.hash) {
          setPending(null); setNeedsAccept(true); onRead(next.review)
          setError(new ApiError(409, { code: 'REVIEW_ALREADY_RESOLVED', message: 'Review уже разрешён другой командой. Ваш черновик сохранён; новая запись не отправляется.' }))
          return
        }
        setReadError(new ApiError(200, { code: 'REVIEW_NOT_CONFIRMED',
          message: 'GET и история не подтвердили исходную команду и её связи. Автоматического повторного POST нет. Повторите чтение.' }))
        return
      }
      setBase(next); setPending(null); setNeedsAccept(false); setError(null)
      setConfirmation({ kind: result, request: attempt.request })
      onRead(next.review)
    } catch (failure) { if (!controller.signal.aborted) setReadError(asError(failure)) }
  }
  async function send(controller: AbortController, attempt: ReviewAttempt) {
    setPending(attempt); setError(null); setConfirmation(null)
    let checkAttempt: ReviewAttempt | null = attempt
    try {
      const receipt = await api.resolveReview(item.review_id, attempt.request, controller.signal)
      if (controller.signal.aborted) return
      checkAttempt = { ...attempt, receipt }; setPending(checkAttempt)
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asError(failure)
      setError(problem); setNeedsAccept(true)
      if ([400, 403, 404, 409, 413, 422, 501].includes(problem.status)) {
        checkAttempt = null; setPending(null)
      }
    }
    if (!controller.signal.aborted) await readLatest(controller, checkAttempt)
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (blocked || !base) return
    if (!author.trim() || !reason.trim()) {
      setError(new ApiError(0, { code: 'FORM_INVALID', message: 'Укажите непустые поля автора и причины решения.' })); return
    }
    const controller = begin('sending')
    if (!controller) return
    try {
      const common = { version: base.review.version, author, reason, topic_versions: versions }
      const request: ResolveReviewRequest = action === 'attach' ? { ...common, action, topic_id: targetId } : { ...common, action }
      const hash = await reviewRequestHash(item.review_id, request)
      if (!controller.signal.aborted) await send(controller, { before: base, request, hash })
    } catch (failure) {
      if (!controller.signal.aborted) setError(asError(failure))
    } finally { finish(controller) }
  }
  async function check() {
    const controller = begin('checking')
    if (!controller) return
    try { await readLatest(controller, pending) } finally { finish(controller) }
  }
  async function repeat() {
    if (!canRepeat || !pending) return
    const controller = begin('sending')
    if (!controller) return
    try { await send(controller, pending) } finally { finish(controller) }
  }

  return <section className="review-decision" aria-label={'Решение Review ' + item.review_id}>
    <h4>Решение и история Review</h4>
    <p>Доступны присоединение к существующей теме и отметка «Недостаточно данных». Создание новой темы появится следующим этапом.</p>
    {busy && <p role="status">{phase === 'loading' ? 'Загружаем Review, историю и каталог тем…' :
      phase === 'sending' ? 'Отправляем решение Review…' : 'Проверяем Review, историю и связи повторным GET…'}</p>}
    <button type="button" disabled={busy} onClick={() => { void check() }}>Проверить Review без повторной записи</button>
    {stale && <p className="topic-notice">Актуальность очереди не подтверждена. Обновите очередь; черновик останется.</p>}
    {(propChanged || needsAccept) && <p className="topic-notice">Состояние или версии могли измениться. Проверьте актуальные данные. Черновик не перезаписан.</p>}
    {canAccept && <button type="button" onClick={() => {
      setBase(latest); setNeedsAccept(false); setError(null); setReadError(null)
    }}>Использовать проверенные версии для нового решения</button>}
    {error && <div role="alert" className="topic-list-error"><p>{error.message}</p>
      <small>{error.status > 0 ? 'HTTP ' + error.status + ' · ' : ''}{error.code}</small></div>}
    {readError && <div role="alert" className="topic-list-error"><p>{readError.message}</p><small>{readError.code}</small></div>}
    {pending && <div className="topic-list-stale"><p><strong>Исходная команда ещё не подтверждена. Новый POST автоматически не отправляется.</strong></p>
      <p>Действие: {pending.request.action === 'attach' ? 'Присоединить к теме ' + pending.request.topic_id : 'Недостаточно данных'} · Автор: {pending.request.author}</p>
      <p className="topic-text">Причина исходной команды: {pending.request.reason}</p>
      {canRepeat && <><p>После успешного чтения Review ещё открыт. По вашей кнопке можно повторить только исходную команду с теми же версиями и полями.
        Backend распознаёт её хеш и не создаёт повторное решение, если запись уже прошла. Новые значения черновика не отправятся.</p>
        <button type="button" onClick={() => { void repeat() }}>Повторить ту же команду</button></>}
    </div>}
    {confirmation && !busy && !readError && !stale && !propChanged && <div role="status" className="review-confirmed">
      <p>{confirmation.kind === 'confirmed'
        ? 'Решение Review подтверждено повторным GET, историей и проверкой связей.'
        : 'Исходное решение найдено в истории, но Review или темы после него изменились. Проверьте актуальные данные.'}</p>
      <p>Подтверждённая команда: {confirmation.request.action === 'attach' ? 'Присоединить к теме ' + confirmation.request.topic_id : 'Недостаточно данных'} · Автор: {confirmation.request.author}.</p>
      <p className="topic-text">Сохранённая причина: {confirmation.request.reason}</p>
      {(author !== confirmation.request.author || reason !== confirmation.request.reason || action !== confirmation.request.action ||
        (confirmation.request.action === 'attach' && targetId !== confirmation.request.topic_id)) && <p>Новые значения черновика не отправлены. Подтверждена исходная команда, указанная выше.</p>}
      <p>Обновите список тем и открытую карточку их кнопками. Фильтры и несохранённые черновики остаются на странице.</p>
      <a href="#topic-list-title">Перейти к темам и карточкам</a>
    </div>}
    {base && <>
      <p>Подготовлена версия Review: {base.review.version} · {base.review.status === 'open' ? 'Открыт' : 'Закрыт'}.</p>
      {base.review.requires_recheck && <p className="topic-notice">Этот Review относится к изменившемуся источнику. Его разрешение запрещено; нужен новый Review после повторной обработки.</p>}
      {latest && latest !== base && <aside className="topic-notice" aria-label="Последние проверенные данные">
        <p>Последний GET: версия {latest.review.version} · {latest.review.status === 'open' ? 'Открыт' : 'Закрыт'}
          {latest.review.requires_recheck ? ' · Требуется перепроверка' : ''}.</p>
        <p>Проверенные версии затрагиваемых тем: {Object.entries(reviewVersions(latest, action, targetId))
          .map(([id, version]) => id + ': ' + version).join('; ') || 'затрагиваемых тем нет'}.</p>
        {action === 'attach' && targetId && (() => {
          const current = latest.topics.find(card => card.topic.topic_id === targetId)
          return current ? <p>Выбранная тема по последнему GET: {current.topic.title} · {topicStatusLabels[current.topic.status]} ·
            {' '}Версия {current.topic.version} · Ответственный: {current.topic.owner ?? 'Не назначен'}.</p> :
            <p>Ранее выбранная тема недоступна. После принятия проверенных версий выберите другую существующую тему.</p>
        })()}
      </aside>}
      <form aria-label={'Форма решения Review ' + item.review_id} onSubmit={event => { void submit(event) }} noValidate>
        <fieldset disabled={busy || closed}><legend>Подготовить решение</legend>
          <div className="topic-actions">
            <button type="button" aria-pressed={action === 'attach'} onClick={() => setAction('attach')}>Присоединить к теме</button>
            <button type="button" aria-pressed={action === 'insufficient_data'} onClick={() => setAction('insufficient_data')}>Недостаточно данных</button>
          </div>
          {action === 'attach' && <><label>Существующая тема<select value={targetId} onChange={event => setTargetId(event.target.value)}>
            <option value="">Выберите тему</option>
            {targetId && !selected && <option value={targetId} disabled>Выбранная тема больше недоступна: {targetId}</option>}
            {base.topics.map(card => <option key={card.topic.topic_id} value={card.topic.topic_id}>{card.topic.title} · {card.topic.topic_id} · v{card.topic.version}</option>)}
          </select></label>{base.topics.length === 0 && <p>Доступных тем нет. Можно отметить недостаточность данных; создание новой темы пока не подключено.</p>}</>}
          {action === 'attach' && selected && <aside className="review-target" aria-label="Выбранная тема">
            <h5>{selected.topic.title}</h5><p>{selected.topic.passport.need_statement}</p><p>Границы: {selected.topic.passport.scope}</p>
            <p>ID: {selected.topic.topic_id} · {topicStatusLabels[selected.topic.status]} · Версия: {selected.topic.version}</p>
            <p>Обращений за всё время: {selected.stats.ticket_count}. Уникальные заявители: {selected.stats.requester_count ?? 'Нет данных'}.</p>
            <p>{selected.synthetic ? 'Синтетические данные.' : 'Не отмечено как синтетическое.'} Сопоставление подтверждает человек.</p>
          </aside>}
          <p>{action === 'attach' ? 'Будут присоединены все потребности этого Review. Прежние назначения этих потребностей будут заменены.' :
            'Новые связи не появятся. Прежние назначения этих потребностей, если они есть, будут сняты; потребности останутся в источниках.'}</p>
          <p>Версии затрагиваемых тем: {Object.entries(versions).map(([id, version]) => id + ': ' + version).join('; ') || 'затрагиваемых тем нет'}.</p>
          <label>Автор решения Review<input required value={author} onChange={event => setAuthor(event.target.value)} /></label>
          <label>Причина решения Review<textarea rows={3} required value={reason} onChange={event => setReason(event.target.value)} /></label>
          <button type="submit" disabled={blocked}>Подтвердить решение Review</button>
        </fieldset>
      </form>
      <details><summary>История Review ({(latest ?? base).history.length})</summary><ol>
        {(latest ?? base).history.map(entry => <li key={entry.version}><p>Версия {entry.version} · {entry.status}</p>
          {entry.resolution && <><p>{entry.resolution.author} · {entry.resolution.action} · {new Date(entry.resolution.resolved_at).toLocaleString('ru-RU')}</p>
            <p className="topic-text">{entry.resolution.reason}</p></>}</li>)}
      </ol></details>
    </>}
  </section>
}
