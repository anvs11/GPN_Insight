import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { DecisionUpdate, TopicDetail } from '../api-types'
import { validDecisionReceipt, verifyDecision } from '../decision-verification'
import { topicStatusLabels } from '../topic-labels'
import './DecisionForm.css'

type Draft = Omit<DecisionUpdate, 'version'>
type Attempt = { before: TopicDetail; request: DecisionUpdate; receipt?: TopicDetail }
type Confirmed = { kind: 'confirmed' | 'changed'; request: DecisionUpdate }

function asError(failure: unknown) {
  return failure instanceof ApiError ? failure : new ApiError(0, {
    code: 'REQUEST_FAILED', message: 'Не удалось выполнить запрос. Проверьте соединение и состояние карточки.',
  })
}
function sameDraft(draft: Draft, request: DecisionUpdate) {
  return draft.status === request.status && draft.owner === request.owner && draft.reason === request.reason
}

export function DecisionForm({ detail, stale, onRead, onBusyChange }: {
  detail: TopicDetail; stale: boolean; onRead: (value: TopicDetail) => void; onBusyChange: (busy: boolean) => void
}) {
  const [base, setBase] = useState(detail)
  const [draft, setDraft] = useState<Draft>(() => ({ status: detail.topic.status === 'new' ? 'new' : 'needs_research', owner: detail.topic.owner ?? '', reason: '' }))
  const [phase, setPhase] = useState<'idle' | 'saving' | 'reading'>('idle')
  const [error, setError] = useState<ApiError | null>(null)
  const [readError, setReadError] = useState<ApiError | null>(null)
  const [pending, setPending] = useState<Attempt | null>(null)
  const [needsReview, setNeedsReview] = useState(false)
  const [latestChecked, setLatestChecked] = useState<number | null>(null)
  const [confirmed, setConfirmed] = useState<Confirmed | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  useEffect(() => () => controllerRef.current?.abort(), [])

  const busy = phase !== 'idle'
  const merged = detail.topic.status === 'merged'
  const cardChanged = detail.topic.version !== base.topic.version
  const alreadySaved = confirmed?.kind === 'confirmed' && sameDraft(draft, confirmed.request)
  const blocked = busy || stale || merged || needsReview || cardChanged || pending !== null || alreadySaved
  const canAcceptVersion = !busy && !stale && !merged && !pending?.receipt &&
    (needsReview ? latestChecked === detail.topic.version : cardChanged)

  function begin(nextPhase: 'saving' | 'reading') {
    // Ref блокирует второй submit ещё до следующего рендера React.
    if (controllerRef.current) return null
    const controller = new AbortController()
    controllerRef.current = controller
    setPhase(nextPhase); onBusyChange(true)
    return controller
  }
  function finish(controller: AbortController) {
    if (controllerRef.current === controller) controllerRef.current = null
    if (!controller.signal.aborted) { setPhase('idle'); onBusyChange(false) }
  }

  async function readLatest(controller: AbortController, attempt: Attempt | null) {
    setPhase('reading'); setReadError(null)
    try {
      const current = await api.topic(detail.topic.topic_id, controller.signal)
      if (controller.signal.aborted) return
      onRead(current)
      setLatestChecked(current.topic.version)
      if (!attempt) return
      const result = verifyDecision(attempt.before, attempt.request, current, attempt.receipt)
      if (result === 'unconfirmed') {
        setNeedsReview(true)
        setReadError(new ApiError(200, { code: 'DECISION_NOT_CONFIRMED',
          message: 'Повторное чтение не подтвердило сохранённые статус, версию и целостность истории. Сверьте карточку; повторная отправка автоматически не выполняется.' }))
        return
      }
      setPending(null); setError(null)
      setConfirmed({ kind: result, request: attempt.request })
      setNeedsReview(result === 'changed')
      if (result === 'confirmed') setBase(current)
    } catch (failure) {
      if (!controller.signal.aborted) { setReadError(asError(failure)); setNeedsReview(true) }
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (blocked || controllerRef.current) return
    if (!draft.owner.trim() || !draft.reason.trim()) {
      setError(new ApiError(0, { code: 'FORM_INVALID', message: 'Заполните ответственного и причину. Поля не должны состоять только из пробелов.' }))
      return
    }
    const controller = begin('saving')
    if (!controller) return
    const request: DecisionUpdate = { ...draft, version: base.topic.version }
    const attempt: Attempt = { before: base, request }
    setError(null); setReadError(null); setConfirmed(null); setLatestChecked(null)
    try {
      const receipt = await api.saveDecision(detail.topic.topic_id, request, controller.signal)
      if (controller.signal.aborted) return
      if (!validDecisionReceipt(base, request, receipt)) throw new ApiError(200, {
        code: 'INVALID_DECISION_RESPONSE', message: 'Ответ сохранения не подтверждает статус, версию или целостность истории. Проверяем карточку повторным чтением.',
      })
      attempt.receipt = receipt
      setPending(attempt)
      await readLatest(controller, attempt)
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asError(failure)
      setError(problem)
      if (problem.status === 409) {
        setNeedsReview(true); setPending(null)
        await readLatest(controller, null)
      } else if (problem.status === 0 || problem.status === 200 || problem.status >= 500) {
        // Потерянный ответ не доказывает откат SQLite. Сначала только GET.
        setPending(attempt); setNeedsReview(true)
        await readLatest(controller, attempt)
      } else {
        setPending(null)
        if (problem.status === 404) setNeedsReview(true)
      }
    } finally {
      finish(controller)
    }
  }

  async function check() {
    const controller = begin('reading')
    if (!controller) return
    try { await readLatest(controller, pending) } finally { finish(controller) }
  }
  function acceptVersion() {
    if (!canAcceptVersion) return
    setBase(detail); setPending(null); setNeedsReview(false); setError(null); setReadError(null); setConfirmed(null)
  }
  function edit(value: Draft) { setDraft(value); setConfirmed(null) }

  return <section className="decision-form" aria-label="Решение владельца продукта">
    <h4>Решение владельца продукта</h4>
    <p>Доступны «Новая» и «Исследуется». Ответственный и причина обязательны. Каждое сохранение, даже без смены статуса, добавляет запись в историю.</p>
    <p>Версия для отправки: <strong>{base.topic.version}</strong>. Версия прочитанной карточки: <strong>{detail.topic.version}</strong>.</p>
    {merged && <p className="topic-notice">Тема объединена. Решение нужно менять в основной теме {detail.topic.canonical_topic_id}.</p>}
    {stale && <p className="topic-notice">Актуальность карточки не подтверждена. Сначала успешно обновите карточку; введённые значения останутся в форме.</p>}
    {(needsReview || cardChanged) && !merged && <div className="topic-notice">
      <p>Сохранение заблокировано до проверки актуальной карточки. Введённые значения сохранены. Сравните статус, ответственного и историю; версия не подставляется незаметно.</p>
      {canAcceptVersion && <button type="button" onClick={acceptVersion}>Использовать версию {detail.topic.version} для нового сохранения</button>}
    </div>}
    {error && <div role="alert" className="topic-list-error">
      <strong>{error.code === 'FORM_INVALID' ? 'Проверьте поля формы.' : error.status === 409 ? 'Конфликт версии или состояния темы.' : error.status === 422 ? 'Сервер отклонил данные решения.' : error.status === 404 ? 'Тема не найдена.' : 'Ответ сохранения не подтверждён.'}</strong>
      <p>{error.message}</p><small>{error.status > 0 ? `HTTP ${error.status} · ` : ''}{error.code}</small>
    </div>}
    {readError && <div role="alert" className="topic-list-error"><strong>Не удалось перечитать карточку.</strong>
      <p>{readError.message}</p><small>{readError.status > 0 ? `HTTP ${readError.status} · ` : ''}{readError.code}</small>
    </div>}
    {pending && <p className="topic-notice">{pending.receipt
      ? 'Сервер ответил на сохранение. Повторное чтение ещё не подтвердило решение; повторный PATCH заблокирован.'
      : 'Сохранение пока не подтверждено. Проверка выполняется чтением карточки, без повторной отправки решения.'}</p>}
    {(needsReview || pending || readError) && <button type="button" disabled={busy} onClick={() => { void check() }}>Проверить карточку без повторного сохранения</button>}
    {confirmed && !stale && (confirmed.kind === 'changed' || !cardChanged) && <p role="status" className={confirmed.kind === 'confirmed' ? 'decision-confirmed' : 'topic-notice'}>
      {confirmed.kind === 'confirmed'
        ? `Сохранение подтверждено повторным чтением: «${topicStatusLabels[confirmed.request.status]}», версия ${confirmed.request.version + 1}. Запись решения и прежняя история сохранены.`
        : `Запись вашего решения найдена в истории версии ${confirmed.request.version + 1}, но после этого карточка изменилась. Проверьте актуальное решение.`}
      {!sameDraft(draft, confirmed.request) && ' Новые значения формы пока не отправлены.'}
    </p>}
    <form onSubmit={event => { void submit(event) }} noValidate>
      <fieldset disabled={busy || merged}>
        <legend>Новое решение</legend>
        <label>Статус решения<select value={draft.status} onChange={event => {
          const status = event.target.value
          if (status === 'new' || status === 'needs_research') edit({ ...draft, status })
        }}><option value="new">Новая</option><option value="needs_research">Исследуется</option></select></label>
        <label>Ответственный<input type="text" required value={draft.owner} onChange={event => edit({ ...draft, owner: event.target.value })} /></label>
        <label>Причина решения<textarea rows={4} required value={draft.reason} onChange={event => edit({ ...draft, reason: event.target.value })} /></label>
        <button type="submit" disabled={blocked}>{phase === 'saving' ? 'Сохраняем решение…' : phase === 'reading' ? 'Проверяем карточку…' : 'Сохранить решение'}</button>
      </fieldset>
    </form>
    {busy && <p role="status">{phase === 'saving' ? 'Отправляем решение серверу…' : 'Перечитываем карточку и проверяем статус, версию и историю…'}</p>}
    <p><small>Несохранённые значения остаются в открытой карточке. Закрытие карточки или перезагрузка страницы сбрасывает черновик.</small></p>
  </section>
}
