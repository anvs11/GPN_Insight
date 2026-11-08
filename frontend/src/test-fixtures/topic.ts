import type { TopicListItem } from '../api-types'

// Синтетическая проекция TopicDetail только для UI/API-тестов, не fallback приложения.
export const topicListItem: TopicListItem = {
  period_stats: null,
  topic: { topic_id: 'synthetic-topic-list', title: 'Контактный телефон в оформленной заявке',
    status: 'needs_research', owner: 'Тестовый владелец' },
  stats: { ticket_count: 4, requester_count: null, support_level: 'repeated' },
  model_mode: 'replay', analysis_mode: null, synthetic: true, data_origin: 'authored', pending_source_ids: [],
}
