import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../api'
import type { PassportFields, PassportUpdate, SourceDetail, TopicChangeEvent, TopicDetail } from '../api-types'
import { isTopicSnapshot } from '../api-validators'
import { changesPreserved, passportEvidence, sameValue, validPassportReceipt, verifyPassport } from '../passport-verification'
import { formatTopicDate } from '../topic-labels'
import { PassportExamples } from './PassportExamples'
import './PassportForm.css'

type Draft = { title: string; author: string; reason: string; passport: PassportFields }
type Attempt = { before: TopicDetail; request: PassportUpdate; changes: TopicChangeEvent[]; receipt?: TopicDetail }
type Confirmed = { kind: 'confirmed' | 'changed'; request: PassportUpdate }
const changeLabels: Record<TopicChangeEvent['kind'], string> = {
  passport: 'Правка паспорта', attachment: 'Добавление свидетельств', manual_override: 'Ручное исправление',
  merge: 'Объединение тем', exclusion: 'Исключение свидетельств',
}
const versionKey = (detail: TopicDetail) => detail.topic.version + '/' + detail.topic.passport.version
function asError(value: unknown) {
  return value instanceof ApiError ? value : new ApiError(0, { code: 'REQUEST_FAILED', message: 'Не удалось проверить паспорт. Проверьте соединение и повторите чтение.' })
}
function draftMatches(draft: Draft, request: PassportUpdate) {
  return sameValue(draft, { title: request.title, author: request.author, reason: request.reason, passport: request.passport })
}

function snapshot(value: Record<string, unknown>) {
  if (!isTopicSnapshot(value)) return null
  const passport = value.passport
  return <div>
    <p>Название: {value.title}</p><p>Сценарий: {passport.scenario}</p>
    <p className="topic-text">Потребность: {passport.need_statement}</p>
    <p className="topic-text">Границы: {passport.scope}</p>
    <p>Границы {passport.scope_confirmed ? 'подтверждены' : 'не подтверждены'}. Версии темы/паспорта: {value.version}/{passport.version}.</p>
    {(['inclusion_examples', 'exclusion_examples'] as const).map(key => <div key={key}>
      <strong>{key === 'inclusion_examples' ? 'Примеры включения' : 'Примеры исключения'}</strong>
      {passport[key].length === 0 ? <p>Не заданы.</p> : <ul>{passport[key].map((example, index) => <li key={index}>
        <p className="topic-text">{example.text}</p>{example.evidence_refs.map((ref, i) =>
          <blockquote key={i}>{ref.quote} · {ref.source_id} / {ref.fragment_id}</blockquote>)}
      </li>)}</ul>}
    </div>)}
  </div>
}

export function PassportForm({ detail, stale, disabled = false, onRead, onBusyChange }: {
  detail: TopicDetail; stale: boolean; disabled?: boolean; onRead: (value: TopicDetail) => void;
  onBusyChange: (busy: boolean) => boolean | void
}) {
  const [base, setBase] = useState(detail)
  const [draft, setDraft] = useState<Draft>(() => {
    const { scenario, need_statement, scope, inclusion_examples, exclusion_examples, scope_confirmed } = detail.topic.passport
    return { title: detail.topic.title, author: '', reason: '', passport: structuredClone({
      scenario, need_statement, scope, inclusion_examples, exclusion_examples, scope_confirmed,
    }) }
  })
  const [phase, setPhase] = useState<'idle' | 'saving' | 'reading'>('idle')
  const [changes, setChanges] = useState<TopicChangeEvent[] | null>(null)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState<ApiError | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [readError, setReadError] = useState<ApiError | null>(null)
  const [pending, setPending] = useState<Attempt | null>(null)
  const [needsReview, setNeedsReview] = useState(false)
  const [latestChecked, setLatestChecked] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState<Confirmed | null>(null)
  const [sourceRevision, setSourceRevision] = useState(0)
  const [sourceResult, setSourceResult] = useState<{ key: string; sources: SourceDetail[]; failed: string[] }>({ key: '', sources: [], failed: [] })
  const controllerRef = useRef<AbortController | null>(null)
  const historyControllerRef = useRef<AbortController | null>(null)
  const draftExamples = [...draft.passport.inclusion_examples, ...draft.passport.exclusion_examples]
  const sourceIds = JSON.stringify([...new Set([
    ...detail.topic.passport.inclusion_examples, ...detail.topic.passport.exclusion_examples, ...draftExamples,
  ].flatMap(example => example.evidence_refs.map(ref => ref.source_id)))].filter(id => !detail.sources.some(source => source.ticket.source_id === id)).sort())
  const sourcesLoading = sourceIds !== '[]' && sourceResult.key !== sourceIds
  const options = passportEvidence(detail, sourceResult.sources, draftExamples)

  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => {
    const controller = new AbortController()
    historyControllerRef.current = controller
    async function load() {
      try {
        const result = await api.topicChanges(detail.topic.topic_id, controller.signal)
        if (!controller.signal.aborted) { setChanges(result); setHistoryError(null) }
      } catch (failure) {
        if (!controller.signal.aborted) setHistoryError(asError(failure))
      } finally {
        if (!controller.signal.aborted) setHistoryLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [detail.topic.topic_id])

  useEffect(() => {
    const ids: string[] = JSON.parse(sourceIds)
    if (ids.length === 0) return
    const controller = new AbortController()
    async function load() {
      const results = await Promise.allSettled(ids.map(id => api.source(id, controller.signal)))
      if (controller.signal.aborted) return
      setSourceResult({ key: sourceIds, sources: results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []),
        failed: ids.filter((_, index) => results[index].status === 'rejected') })
    }
    void load()
    return () => controller.abort()
  }, [sourceIds, sourceRevision])

  const busy = phase !== 'idle'
  const merged = detail.topic.status === 'merged'
  const cardChanged = versionKey(detail) !== versionKey(base)
  const alreadySaved = confirmed?.kind === 'confirmed' && draftMatches(draft, confirmed.request)
  const blocked = busy || disabled || stale || merged || cardChanged || needsReview || pending !== null ||
    alreadySaved || historyLoading || changes === null || historyError !== null || sourcesLoading
  const canAccept = !busy && !disabled && !stale && !merged && !pending?.receipt &&
    (needsReview ? latestChecked === versionKey(detail) : cardChanged)

  function edit(value: Draft) { setDraft(value); setConfirmed(null) }
  function begin(next: 'saving' | 'reading') {
    if (controllerRef.current || onBusyChange(true) === false) return null
    const controller = new AbortController()
    controllerRef.current = controller
    historyControllerRef.current?.abort()
    setHistoryLoading(false); setPhase(next)
    return controller
  }
  function finish(controller: AbortController) {
    if (controllerRef.current === controller) controllerRef.current = null
    if (!controller.signal.aborted) { setPhase('idle'); onBusyChange(false) }
  }
  async function readLatest(controller: AbortController, attempt: Attempt | null) {
    setPhase('reading'); setReadError(null)
    try {
      const [current, history] = await Promise.all([
        api.topic(detail.topic.topic_id, controller.signal), api.topicChanges(detail.topic.topic_id, controller.signal),
      ])
      if (controller.signal.aborted) return
      if (changes && !changesPreserved(changes, history)) throw new ApiError(200, {
        code: 'CHANGE_HISTORY_NOT_CONFIRMED', message: 'Прежняя история изменений не подтверждена. Сохранение заблокировано; повторите чтение.',
      })
      setChanges(history); setHistoryError(null); setLatestChecked(versionKey(current)); onRead(current)
      if (!attempt) return
      const result = verifyPassport(attempt.before, attempt.request, current, attempt.changes, history, attempt.receipt)
      if (result === 'unconfirmed') {
        setNeedsReview(true)
        setReadError(new ApiError(200, { code: 'PASSPORT_NOT_CONFIRMED',
          message: 'Карточка и история не подтвердили запись паспорта и обе версии. Автоматического повторного PATCH нет. Проверьте данные и повторите чтение.' }))
        return
      }
      setPending(null); setError(null); setConfirmed({ kind: result, request: attempt.request }); setNeedsReview(result === 'changed')
      if (result === 'confirmed') setBase(current)
    } catch (failure) {
      if (!controller.signal.aborted) { setReadError(asError(failure)); setNeedsReview(true) }
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (blocked || controllerRef.current) return
    const examples = [...draft.passport.inclusion_examples, ...draft.passport.exclusion_examples]
    const textFields = [draft.title, draft.author, draft.reason, draft.passport.scenario, draft.passport.need_statement, draft.passport.scope]
    if (textFields.some(value => !value.trim()) || draft.passport.inclusion_examples.length === 0 ||
      examples.some(example => !example.text.trim() || example.evidence_refs.length === 0 ||
        example.evidence_refs.some(ref => !options.some(option => option.verified && sameValue(option.ref, ref))))) {
      setError(new ApiError(0, { code: 'FORM_INVALID',
        message: 'Заполните название, сценарий, потребность, границы, автора и причину. Нужен хотя бы один пример включения; каждому примеру нужны текст и проверенные цитаты.' }))
      return
    }
    const controller = begin('saving')
    if (!controller) return
    setError(null); setReadError(null); setConfirmed(null); setLatestChecked(null)
    let attempt: Attempt | null = null
    try {
      // Сначала читаем историю. Её отказ не должен приводить к PATCH.
      const history = await api.topicChanges(detail.topic.topic_id, controller.signal)
      if (controller.signal.aborted) return
      if (!changesPreserved(changes ?? [], history)) throw new ApiError(200, {
        code: 'CHANGE_HISTORY_NOT_CONFIRMED', message: 'Прежняя история не подтверждена; паспорт не отправлен.',
      })
      setChanges(history); setHistoryError(null)
      const request: PassportUpdate = { ...structuredClone(draft), version: base.topic.version, passport_version: base.topic.passport.version }
      attempt = { before: base, request, changes: history }
      setPending(attempt)
      const receipt = await api.savePassport(detail.topic.topic_id, request, controller.signal)
      if (controller.signal.aborted) return
      if (!validPassportReceipt(base, request, receipt)) throw new ApiError(200, {
        code: 'INVALID_PASSPORT_RESPONSE', message: 'Ответ PATCH не подтвердил версии или сохранность данных. Проверяем карточку и историю.',
      })
      attempt.receipt = receipt
      setPending({ ...attempt })
      await readLatest(controller, attempt)
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asError(failure)
      setError(problem)
      if (!attempt) {
        if (problem.status === 200) setNeedsReview(true)
      } else if (problem.status === 409) {
        setPending(null); setNeedsReview(true)
        await readLatest(controller, null)
      } else if (problem.status === 0 || problem.status === 200 || problem.status >= 500) {
        setPending(attempt); setNeedsReview(true)
        await readLatest(controller, attempt)
      } else {
        setPending(null)
        if (problem.status === 404) setNeedsReview(true)
      }
    } finally { finish(controller) }
  }
  async function check() {
    const controller = begin('reading')
    if (!controller) return
    try { await readLatest(controller, pending) } finally { finish(controller) }
  }

  return <section className="passport-form" aria-label="Редактирование паспорта темы">
    <h4>Редактирование паспорта</h4>
    <p>Паспорт описывает потребность и её границы. Правка не меняет бизнес-решение. Цитаты выбираются из существующих источников; смысл примеров проверяет автор.</p>
    <p>Версии для отправки — тема: <strong>{base.topic.version}</strong>, паспорт: <strong>{base.topic.passport.version}</strong>.
      Прочитаны версии: {detail.topic.version}/{detail.topic.passport.version}.</p>
    {merged && <p className="topic-notice">Тема объединена. Паспорт меняется в основной теме {detail.topic.canonical_topic_id}.</p>}
    {stale && <p className="topic-notice">Актуальность карточки не подтверждена. Сначала обновите её; черновик паспорта останется.</p>}
    {disabled && <p className="topic-notice">Сначала завершите сохранение решения.</p>}
    {(needsReview || cardChanged) && !merged && <div className="topic-notice">
      <p>Проверьте актуальную карточку и историю. Черновик сохранён, версии не подставляются автоматически.</p>
      {canAccept && <button type="button" onClick={() => {
        setBase(detail); setPending(null); setNeedsReview(false); setError(null); setReadError(null); setConfirmed(null)
      }}>Использовать версии {detail.topic.version}/{detail.topic.passport.version} для новой правки паспорта</button>}
    </div>}
    {error && <div role="alert" className="topic-list-error"><strong>{error.code === 'FORM_INVALID' ? 'Проверьте поля паспорта.' :
      error.status === 409 ? 'Конфликт версий или состояния паспорта.' : error.status === 422 ? 'Сервер отклонил паспорт.' :
      error.status === 404 ? 'Тема не найдена.' : 'Сохранение паспорта не подтверждено.'}</strong>
      <p>{error.message}</p><small>{error.status > 0 ? 'HTTP ' + error.status + ' · ' : ''}{error.code}</small></div>}
    {readError && <div role="alert" className="topic-list-error"><strong>Проверка паспорта не завершена.</strong>
      <p>{readError.message}</p><small>{readError.code}</small></div>}
    {pending && <p className="topic-notice">Запись ещё не подтверждена карточкой и историей. Повторный PATCH заблокирован.</p>}
    {(pending || needsReview || readError) && <button type="button" disabled={busy || disabled} onClick={() => { void check() }}>Проверить паспорт без повторной записи</button>}
    {confirmed && !busy && !stale && !readError && !historyError && (confirmed.kind === 'changed' || !cardChanged) && <p role="status" className="passport-confirmed">
      {confirmed.kind === 'confirmed' ? 'Сохранение паспорта подтверждено карточкой и историей изменений. Обе версии повышены, решение и прежняя история сохранены.' :
        'Ваша правка найдена в истории, но после неё тема изменилась. Проверьте актуальный паспорт и решение.'}
    </p>}
    {sourcesLoading && <p role="status">Проверяем источники примеров…</p>}
    {sourceResult.key === sourceIds && sourceResult.failed.length > 0 && <div className="topic-notice"><p>Не удалось проверить источники: {sourceResult.failed.join(', ')}.
      Непроверенные цитаты нельзя отправлять.</p><button type="button" disabled={busy || disabled} onClick={() => {
        setSourceResult(previous => ({ ...previous, key: '' })); setSourceRevision(value => value + 1)
      }}>Повторить проверку цитат</button></div>}
    <form aria-label="Форма паспорта" onSubmit={event => { void submit(event) }} noValidate>
      <fieldset disabled={busy || disabled || merged}>
        <legend>Новая правка паспорта</legend>
        <label>Название темы<input required value={draft.title} onChange={event => edit({ ...draft, title: event.target.value })} /></label>
        {(['scenario', 'need_statement', 'scope'] as const).map((key, index) => <label key={key}>
          {['Сценарий паспорта', 'Потребность паспорта', 'Границы паспорта'][index]}<textarea rows={3} required value={draft.passport[key]}
            onChange={event => edit({ ...draft, passport: { ...draft.passport, [key]: event.target.value } })} />
        </label>)}
        <label className="passport-confirm-scope"><input type="checkbox" checked={draft.passport.scope_confirmed}
          onChange={event => edit({ ...draft, passport: { ...draft.passport, scope_confirmed: event.target.checked } })} />Границы паспорта проверены</label>
        <PassportExamples kind="inclusion" examples={draft.passport.inclusion_examples} options={options}
          onChange={value => edit({ ...draft, passport: { ...draft.passport, inclusion_examples: value } })} />
        <PassportExamples kind="exclusion" examples={draft.passport.exclusion_examples} options={options}
          onChange={value => edit({ ...draft, passport: { ...draft.passport, exclusion_examples: value } })} />
        <label>Автор изменения паспорта<input required value={draft.author} onChange={event => edit({ ...draft, author: event.target.value })} /></label>
        <label>Причина изменения паспорта<textarea rows={3} required value={draft.reason} onChange={event => edit({ ...draft, reason: event.target.value })} /></label>
        <button type="submit" disabled={blocked}>{busy ? 'Проверяем сохранение паспорта…' : 'Сохранить паспорт'}</button>
      </fieldset>
    </form>
    <p><small>Черновик хранится в открытой карточке и сохраняется при ошибках и смене фильтров. Закрытие карточки или перезагрузка страницы сбрасывает несохранённые значения.</small></p>
    {historyLoading && <p role="status">Загружаем историю изменений…</p>}
    {historyError && <div role="alert" className="topic-list-error"><p>{historyError.message}</p><small>{historyError.code}</small>
      <p>{changes === null ? 'История не получена. Сохранение заблокировано до успешного чтения.' : 'Показана последняя прочитанная история; сохранение заблокировано до успешного чтения.'}</p></div>}
    <button type="button" disabled={busy || disabled || historyLoading} onClick={() => { void check() }}>Обновить историю изменений</button>
    {changes !== null && <details><summary>История изменений темы ({changes.length})</summary>
      {changes.length === 0 ? <p>Изменений пока нет.</p> : <ol>{changes.map(event => <li key={event.event_id}>
        <p>{formatTopicDate(event.occurred_at)} МСК · {changeLabels[event.kind]}</p>
        <p>Автор: {event.author}</p><p className="topic-text">Причина: {event.reason}</p>
        {event.kind === 'passport' && <details><summary>Паспорт до и после правки</summary><h5>До правки</h5>{snapshot(event.before)}<h5>После правки</h5>{snapshot(event.after)}</details>}
      </li>)}</ol>}
    </details>}
  </section>
}
