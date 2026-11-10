import type { DecisionUpdate, SourceDetail, TopicDetail } from '../api-types'
import { topicListItem } from './topic'

// Только синтетические ответы для тестов. Приложение не импортирует эти данные.
export const sourceDetail: SourceDetail = {
  ticket: { source_id: 'SYNTHETIC-001', product_id: 'webneft', created_at: '2026-11-01T10:00:00+03:00',
    text: 'Покажите контактный телефон в заявке.', requester_id: null, assets: [] },
  document: { source_id: 'SYNTHETIC-001', product_id: 'webneft', content_hash: 'a'.repeat(64), version: 1,
    normalization_version: 'synthetic-text-v1', fragments: [{ fragment_id: 'text-1', asset_id: null, kind: 'text',
      text: 'Покажите контактный телефон в заявке.', start_ms: null, end_ms: null, warning: null }], errors: [] },
  needs: [{ need_id: 'synthetic-need-1', source_id: 'SYNTHETIC-001', fragment_id: 'text-1',
    evidence_quote: 'Покажите контактный телефон', scenario: 'Просмотр заявки', need_type: 'feature_request',
    statement: 'Видеть контактный телефон', explicit_request: true, improvement_hypothesis: null, uncertainty_reason: null }],
  source_version: 1, content_hash: 'a'.repeat(64), requires_analysis: false, synthetic: true, data_origin: 'authored',
}
export const topicDetail: TopicDetail = {
  ...topicListItem,
  topic: { ...topicListItem.topic, passport: { product_id: 'webneft', scenario: 'Просмотр заявки',
    need_statement: 'Проверять контактный телефон в оформленной заявке', scope: 'Просмотр уже оформленной заявки',
    inclusion_examples: [], exclusion_examples: [], scope_confirmed: false, version: 1 },
    summary: 'Сохранённое описание потребности', need_ids: ['synthetic-need-1'],
    evidence: [{ source_id: 'SYNTHETIC-001', fragment_id: 'text-1', quote: 'Покажите контактный телефон' }],
    version: 2, canonical_topic_id: null },
  stats: { ...topicListItem.stats, weekly_counts: { '2026-10-26': 4 }, first_seen: '2026-11-01T10:00:00+03:00',
    last_seen: '2026-11-01T10:00:00+03:00', new_ticket_count_since_decision: 0,
    post_implementation_ticket_count: 0, review_due: false },
  sources: [sourceDetail], history: [{ event_id: 'synthetic-decision-1', topic_id: topicListItem.topic.topic_id,
    previous_status: 'new', new_status: 'needs_research', owner: 'Тестовый владелец', reason: 'Сохранённая причина решения',
    existing_issue_ref: null, review_after: null, implemented_at: null, implementation_comment: null,
    occurred_at: '2026-11-02T10:00:00+03:00', topic_version: 2 }],
}

export function savedDecision(before: TopicDetail, request: DecisionUpdate, eventId = 'synthetic-saved-decision'): TopicDetail {
  return { ...before, topic: { ...before.topic, status: request.status, owner: request.owner, version: request.version + 1 },
    history: [...before.history, { event_id: eventId, topic_id: before.topic.topic_id, previous_status: before.topic.status,
      new_status: request.status, owner: request.owner, reason: request.reason, existing_issue_ref: request.existing_issue_ref ?? null, review_after: request.review_after ?? null,
      implemented_at: request.implemented_at ?? null, implementation_comment: request.implementation_comment ?? null, occurred_at: '2026-11-06T10:00:00+03:00', topic_version: request.version + 1 }] }
}
