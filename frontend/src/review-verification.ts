import type { ResolveReviewRequest, ReviewItem, SourceDetail, TopicChangeEvent, TopicDetail } from './api-types'
import { isTopicSnapshot } from './api-validators'
import { proposalProblem } from './review-create'
import { sameValue } from './passport-verification'

export interface ReviewContext {
  review: ReviewItem; history: ReviewItem[]; topics: TopicDetail[]
  sources?: SourceDetail[]
  createdChanges?: TopicChangeEvent[]
}
export interface ReviewAttempt {
  before: ReviewContext
  request: ResolveReviewRequest
  hash: string
  receipt?: ReviewItem
}

function compareUnicode(a: string, b: string): number {
  const left = Array.from(a, char => char.codePointAt(0)!), right = Array.from(b, char => char.codePointAt(0)!)
  for (let i = 0; i < Math.min(left.length, right.length); i++) if (left[i] !== right[i]) return left[i] - right[i]
  return left.length - right.length
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return '{' + Object.keys(record).sort(compareUnicode).map(key => JSON.stringify(key) + ':' + canonical(record[key])).join(',') + '}'
  }
  return JSON.stringify(value)
}
export async function reviewRequestHash(id: string, request: ResolveReviewRequest) {
  // Тот же semantic_hash, что HumanWorkflow._command_hash. Нужен для
  // распознавания именно исходной команды после потери HTTP-ответа.
  const value = { operation: 'resolve-review', id, request: {
    ...request, topic_id: request.action === 'attach' ? request.topic_id : null, proposed_topic: request.action === 'create' ? request.proposed_topic : null,
  } }
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value)))
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('')
}
export function reviewVersions(context: ReviewContext, action: ResolveReviewRequest['action'], targetId: string) {
  return Object.fromEntries(context.topics.filter(card => card.topic.need_ids.some(id => context.review.need_ids.includes(id)) ||
    (action === 'attach' && card.topic.topic_id === targetId)).map(card => [card.topic.topic_id, card.topic.version]))
}
export function contextKey(context: ReviewContext) {
  return canonical({ review: context.review, topics: context.topics.map(card => [card.topic.topic_id, card.topic.version]) })
}
export function historyPreserved(before: ReviewItem[], after: ReviewItem[]) {
  return before.every(entry => after.some(current => current.version === entry.version && sameValue(current, entry)))
}
export function reviewReadConsistent(review: ReviewItem, history: ReviewItem[]) {
  const latest = history.at(-1)
  return Boolean(latest && sameValue({ ...review, requires_recheck: false }, { ...latest, requires_recheck: false }))
}
function snapshotMatches(value: unknown, expected: TopicDetail['topic']) {
  if (!isTopicSnapshot(value)) return false
  const normalize = (topic: TopicDetail['topic']) => ({ ...topic, need_ids: [...topic.need_ids].sort(),
    evidence: topic.evidence.map(canonical).sort(compareUnicode) })
  return sameValue(normalize(value), normalize(expected))
}
function matches(attempt: ReviewAttempt, item: ReviewItem) {
  const { before, request, hash } = attempt
  const resolution = item.resolution
  return item.version === request.version + 1 && resolution?.action === request.action &&
    resolution.author === request.author && resolution.reason === request.reason && resolution.request_hash === hash &&
    (request.action === 'create' ? Boolean(resolution.topic_id && !before.topics.some(card => card.topic.topic_id === resolution.topic_id)) :
      resolution.topic_id === (request.action === 'attach' ? request.topic_id : null)) &&
    item.status === (request.action === 'insufficient_data' ? 'insufficient_data' : 'resolved') &&
    sameValue({ ...item, version: before.review.version, status: before.review.status, resolution: before.review.resolution, requires_recheck: false },
      { ...before.review, requires_recheck: false })
}
export function verifyReview(attempt: ReviewAttempt, current: ReviewContext): 'confirmed' | 'changed' | 'unconfirmed' {
  if (attempt.request.version !== attempt.before.review.version ||
    !historyPreserved(attempt.before.history, current.history) || !reviewReadConsistent(current.review, current.history) ||
    (attempt.receipt && !matches(attempt, attempt.receipt))) return 'unconfirmed'
  const saved = current.history.find(item => matches(attempt, item))
  if (!saved || current.review.version < saved.version) return 'unconfirmed'
  if (current.review.version > saved.version || current.review.requires_recheck) return 'changed'
  if (!matches(attempt, current.review) || (attempt.receipt &&
    attempt.receipt.resolution?.topic_id !== saved.resolution?.topic_id)) return 'unconfirmed'
  let changed = false
  for (const [id, version] of Object.entries(attempt.request.topic_versions)) {
    const before = attempt.before.topics.find(card => card.topic.topic_id === id)
    const after = current.topics.find(card => card.topic.topic_id === id)
    if (!before) return 'unconfirmed'
    if (!after) { changed = true; continue } // Например, последующее объединение темы.
    if (!before.history.every(event => after.history.some(item => sameValue(item, event)))) return 'unconfirmed'
    const selected = attempt.request.action === 'attach' && attempt.request.topic_id === id
    const bumps = attempt.before.review.need_ids.filter(need => selected || before.topic.need_ids.includes(need)).length
    if (after.topic.version < version + bumps) return 'unconfirmed'
    if (after.topic.version > version + bumps) { changed = true; continue }
    const needs = selected ? [...new Set([...before.topic.need_ids, ...attempt.before.review.need_ids])] :
      before.topic.need_ids.filter(need => !attempt.before.review.need_ids.includes(need))
    if (!sameValue([...after.topic.need_ids].sort(), needs.sort()) ||
      !sameValue(after.topic.passport, before.topic.passport) || after.topic.status !== before.topic.status ||
      after.topic.owner !== before.topic.owner || after.topic.title !== before.topic.title) return 'unconfirmed'
  }
  if (attempt.request.action === 'create') {
    const id = saved.resolution?.topic_id
    const card = current.topics.find(item => item.topic.topic_id === id)
    if (!card) return 'unconfirmed'
    const expectedVersion = 1 + attempt.before.review.need_ids.length
    if (card.topic.version < expectedVersion) return 'unconfirmed'
    const events = current.createdChanges
    if (!events || attempt.before.review.need_ids.some((_, index) => !events.some(event =>
      event.topic_id === id && event.kind === 'manual_override' &&
      event.author === attempt.request.author && event.reason === attempt.request.reason &&
      snapshotMatches(event.before, { ...card.topic, version: index + 1 }) &&
      snapshotMatches(event.after, { ...card.topic, version: index + 2 })))) {
      // Позднейшее изменение нельзя выдавать за неизменённый результат нашей команды.
      return card.topic.version > expectedVersion ? 'changed' : 'unconfirmed'
    }
    if (card.topic.version > expectedVersion) return 'changed'
    const p = attempt.request.proposed_topic
    if (card.topic.title !== p.title || card.topic.summary !== p.summary ||
      !sameValue(card.topic.passport, { product_id: 'webneft', scenario: p.scenario, need_statement: p.need_statement,
        scope: p.scope, inclusion_examples: p.inclusion_examples, exclusion_examples: p.exclusion_examples,
        version: 1, scope_confirmed: false }) ||
      card.topic.status !== 'new' || card.topic.owner !== null || card.topic.canonical_topic_id !== null ||
      card.data_origin !== 'authored' || card.analysis_mode !== null || card.pending_source_ids.length ||
      card.history.length || !sameValue([...card.topic.need_ids].sort(), [...attempt.before.review.need_ids].sort()) ||
      !sameValue(card.topic.evidence.map(canonical).sort(compareUnicode), attempt.before.review.evidence_refs.map(canonical).sort(compareUnicode)) ||
      proposalProblem(p, attempt.before.review, card.sources) !== null ||
      !sameValue([...new Set(card.sources.map(source => source.ticket.source_id))].sort(),
        [...new Set(attempt.before.review.evidence_refs.map(ref => ref.source_id))].sort()) ||
      card.stats.ticket_count !== new Set(attempt.before.review.evidence_refs.map(ref => ref.source_id)).size) return 'unconfirmed'
    for (const before of attempt.before.topics) {
      const after = current.topics.find(item => item.topic.topic_id === before.topic.topic_id)
      if (!after) { changed = true; continue }
      if (!before.history.every(event => after.history.some(item => sameValue(item, event)))) return 'unconfirmed'
      if (!(before.topic.topic_id in attempt.request.topic_versions)) {
        if (after.topic.version !== before.topic.version) changed = true
        else if (!sameValue(before.topic, after.topic)) return 'unconfirmed'
      }
    }
  }
  return changed ? 'changed' : 'confirmed'
}
