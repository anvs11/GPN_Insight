import type { ResolveReviewRequest, ReviewItem } from '../api-types'
import type { ReviewContext } from '../review-verification'
import { topicDetail } from './detail'
import { reviewItem } from './import'

// Синтетические образцы для проверки UI/контрактов; не ответы живой модели.
export const reviewTarget = structuredClone(topicDetail)
reviewTarget.topic.topic_id = 'synthetic-topic'
reviewTarget.topic.version = 3
export const reviewContext: ReviewContext = { review: reviewItem, history: [reviewItem], topics: [reviewTarget] }
export const attachRequest: ResolveReviewRequest = {
  version: 1, action: 'attach', author: 'Синтетический аналитик', reason: 'Проверены источники',
  topic_id: 'synthetic-topic', topic_versions: { 'synthetic-topic': 3 },
}
export function resolvedContext(request: ResolveReviewRequest, hash: string, before = reviewContext): ReviewContext {
  const review: ReviewItem = { ...before.review, version: request.version + 1,
    status: request.action === 'attach' ? 'resolved' : 'insufficient_data',
    resolution: { action: request.action, author: request.author, reason: request.reason, request_hash: hash,
      topic_id: request.action === 'attach' ? request.topic_id : null, resolved_at: '2026-11-09T12:00:00+03:00' } }
  const topics = structuredClone(before.topics).map(card => {
    if (!(card.topic.topic_id in request.topic_versions)) return card
    const selected = request.action === 'attach' && card.topic.topic_id === request.topic_id
    const bumps = before.review.need_ids.filter(id => selected || card.topic.need_ids.includes(id)).length
    card.topic.version += bumps
    card.topic.need_ids = selected ? [...new Set([...card.topic.need_ids, ...before.review.need_ids])] :
      card.topic.need_ids.filter(id => !before.review.need_ids.includes(id))
    return card
  })
  return { review, history: [...before.history, review], topics }
}
