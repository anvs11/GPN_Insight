import type { PassportUpdate, TopicChangeEvent, TopicDetail } from '../api-types'
import { topicDetail } from './detail'

// Только synthetic данные тестов, приложение этот файл не импортирует.
export const passportDetail: TopicDetail = { ...topicDetail, topic: { ...topicDetail.topic,
  passport: { ...topicDetail.topic.passport, inclusion_examples: [{
    text: 'Проверенный пример', evidence_refs: [topicDetail.topic.evidence[0]],
  }] },
} }

export function passportRequest(before: TopicDetail = passportDetail): PassportUpdate {
  const { scenario, need_statement, scope, inclusion_examples, exclusion_examples } = before.topic.passport
  return { version: before.topic.version, passport_version: before.topic.passport.version,
    title: 'Уточнённая синтетическая тема', author: 'Синтетический аналитик', reason: 'Проверены границы',
    passport: structuredClone({ scenario, need_statement, scope, inclusion_examples, exclusion_examples, scope_confirmed: true }) }
}

export function savedPassport(before: TopicDetail, request: PassportUpdate): TopicDetail {
  return { ...before, data_origin: 'authored', topic: { ...before.topic,
    title: request.title ?? before.topic.title, version: request.version + 1,
    passport: { ...request.passport, product_id: before.topic.passport.product_id, version: request.passport_version + 1 },
  } }
}

export function passportChange(before: TopicDetail, request: PassportUpdate, eventId = 'synthetic-passport-change'): TopicChangeEvent {
  return { event_id: eventId, topic_id: before.topic.topic_id, kind: 'passport', author: request.author, reason: request.reason,
    occurred_at: '2026-11-08T12:00:00+03:00', before: { ...before.topic }, after: { ...savedPassport(before, request).topic },
    request_hash: null, result: null }
}
