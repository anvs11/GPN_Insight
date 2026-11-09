import type { ProposedTopic, ResolveReviewRequest, ReviewItem, SourceDetail, TopicChangeEvent } from '../api-types'
import type { ReviewContext } from '../review-verification'
import { sourceDetail, topicDetail } from './detail'
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
    status: request.action === 'insufficient_data' ? 'insufficient_data' : 'resolved',
    resolution: { action: request.action, author: request.author, reason: request.reason, request_hash: hash,
      topic_id: request.action === 'attach' ? request.topic_id : request.action === 'create' ? 'synthetic-created-topic' : null, resolved_at: '2026-11-09T12:00:00+03:00' } }
  const topics = structuredClone(before.topics).map(card => {
    if (!(card.topic.topic_id in request.topic_versions)) return card
    const selected = request.action === 'attach' && card.topic.topic_id === request.topic_id
    const bumps = before.review.need_ids.filter(id => selected || card.topic.need_ids.includes(id)).length
    card.topic.version += bumps
    card.topic.need_ids = selected ? [...new Set([...card.topic.need_ids, ...before.review.need_ids])] :
      card.topic.need_ids.filter(id => !before.review.need_ids.includes(id))
    return card
  })
  const createdChanges: TopicChangeEvent[] = []
  if (request.action === 'create') {
    const p = request.proposed_topic
    const card = structuredClone(reviewTarget)
    card.topic = { topic_id: 'synthetic-created-topic', title: p.title, summary: p.summary,
      passport: { product_id: 'webneft', scenario: p.scenario, need_statement: p.need_statement, scope: p.scope,
        inclusion_examples: p.inclusion_examples, exclusion_examples: p.exclusion_examples, version: 1, scope_confirmed: false },
      version: 1 + before.review.need_ids.length, status: 'new', owner: null, canonical_topic_id: null,
      need_ids: [...before.review.need_ids], evidence: before.review.evidence_refs }
    card.history = []; card.sources = [reviewSource]; card.pending_source_ids = []
    card.data_origin = 'authored'; card.analysis_mode = null
    card.stats.ticket_count = 1
    for (let i = 0; i < before.review.need_ids.length; i++) createdChanges.push({
      event_id: 'synthetic-create-event-' + i, topic_id: card.topic.topic_id, kind: 'manual_override',
      author: request.author, reason: request.reason, occurred_at: '2026-11-09T12:00:00+03:00',
      before: { ...card.topic, version: i + 1 }, after: { ...card.topic, version: i + 2 }, request_hash: null, result: null,
    })
    topics.push(card)
  }
  return { review, history: [...before.history, review], topics, createdChanges }
}

export const reviewSource: SourceDetail = structuredClone(sourceDetail)
reviewSource.ticket.source_id = 'synthetic-source'
reviewSource.ticket.text = 'Изменить контакт в заявке'
reviewSource.document!.source_id = 'synthetic-source'
reviewSource.document!.fragments[0] = { ...reviewSource.document!.fragments[0],
  fragment_id: 'synthetic-fragment', text: 'Изменить контакт в заявке' }
reviewSource.needs = [{ ...reviewSource.needs[0], need_id: 'synthetic-need', source_id: 'synthetic-source',
  fragment_id: 'synthetic-fragment', evidence_quote: 'Изменить контакт в заявке' }]
export const proposedTopic: ProposedTopic = {
  title: 'Синтетическая новая тема', summary: 'Уточнение изменения контакта',
  scenario: 'Просмотр заявки', need_statement: 'Уточнить изменение контакта',
  scope: 'Изменение контакта; не включает экспорт отчётов',
  inclusion_examples: [{ text: 'Просьба изменить контакт', evidence_refs: reviewItem.evidence_refs }],
  exclusion_examples: [],
}
export const createRequest: ResolveReviewRequest & { action: 'create' } = {
  version: 1, action: 'create', author: attachRequest.author, reason: attachRequest.reason,
  topic_versions: {}, proposed_topic: proposedTopic,
}
