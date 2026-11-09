import type { ResolveReviewRequest, ReviewItem, TopicDetail } from './api-types'
import { sameValue } from './passport-verification'

export interface ReviewContext { review: ReviewItem; history: ReviewItem[]; topics: TopicDetail[] }
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
    ...request, topic_id: request.action === 'attach' ? request.topic_id : null, proposed_topic: null,
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
function matches(attempt: ReviewAttempt, item: ReviewItem) {
  const { before, request, hash } = attempt
  const resolution = item.resolution
  return item.version === request.version + 1 && resolution?.action === request.action &&
    resolution.author === request.author && resolution.reason === request.reason && resolution.request_hash === hash &&
    resolution.topic_id === (request.action === 'attach' ? request.topic_id : null) &&
    item.status === (request.action === 'attach' ? 'resolved' : 'insufficient_data') &&
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
  if (!matches(attempt, current.review)) return 'unconfirmed'
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
  return changed ? 'changed' : 'confirmed'
}
