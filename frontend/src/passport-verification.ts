import type { EvidenceRef, PassportExample, PassportUpdate, SourceDetail, TopicChangeEvent, TopicDetail } from './api-types'
import { isTopicSnapshot } from './api-validators'

export function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right) &&
    left.length === right.length && left.every((item, index) => sameValue(item, right[index]))
  if (typeof left !== 'object' || left === null || typeof right !== 'object' || right === null) return false
  const a = left as Record<string, unknown>, b = right as Record<string, unknown>
  return Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every(key => Object.hasOwn(b, key) && sameValue(a[key], b[key]))
}

export function changesPreserved(before: TopicChangeEvent[], after: TopicChangeEvent[]) {
  return before.every(event => after.some(current => current.event_id === event.event_id && sameValue(current, event)))
}

function expectedTopic(before: TopicDetail, request: PassportUpdate) {
  return { ...before.topic, title: request.title ?? before.topic.title, version: request.version + 1,
    passport: { ...request.passport, product_id: before.topic.passport.product_id, version: request.passport_version + 1 } }
}

export function validPassportReceipt(before: TopicDetail, request: PassportUpdate, receipt: TopicDetail) {
  return request.version === before.topic.version && request.passport_version === before.topic.passport.version &&
    sameValue(receipt.topic, expectedTopic(before, request)) && sameValue(before.history, receipt.history) &&
    sameValue(before.stats, receipt.stats) && sameValue(before.sources, receipt.sources) &&
    sameValue(before.pending_source_ids, receipt.pending_source_ids) && receipt.synthetic === before.synthetic &&
    receipt.analysis_mode === before.analysis_mode && receipt.model_mode === before.model_mode && receipt.data_origin === 'authored'
}

export function verifyPassport(before: TopicDetail, request: PassportUpdate, current: TopicDetail,
  previousChanges: TopicChangeEvent[], changes: TopicChangeEvent[], receipt?: TopicDetail): 'confirmed' | 'changed' | 'unconfirmed' {
  if (current.topic.topic_id !== before.topic.topic_id || request.version !== before.topic.version ||
    request.passport_version !== before.topic.passport.version || !changesPreserved(previousChanges, changes) ||
    !before.history.every(event => current.history.some(item => item.event_id === event.event_id && sameValue(item, event)))) return 'unconfirmed'
  const event = changes.find(item => item.kind === 'passport' && item.topic_id === before.topic.topic_id &&
    item.author === request.author && item.reason === request.reason && sameValue(item.before, before.topic) &&
    sameValue(item.after, expectedTopic(before, request)))
  if (!event || !isTopicSnapshot(event.after) || current.topic.version < event.after.version ||
    current.topic.passport.version < event.after.passport.version ||
    (receipt && !validPassportReceipt(before, request, receipt))) return 'unconfirmed'
  if (current.topic.version > event.after.version) return 'changed'
  return validPassportReceipt(before, request, current) ? 'confirmed' : 'unconfirmed'
}

export function passportEvidence(detail: TopicDetail, extraSources: SourceDetail[], draftExamples: PassportExample[]) {
  const sources = [...extraSources, ...detail.sources]
  const refs = [...detail.topic.evidence,
    ...detail.topic.passport.inclusion_examples.flatMap(item => item.evidence_refs),
    ...detail.topic.passport.exclusion_examples.flatMap(item => item.evidence_refs),
    ...draftExamples.flatMap(item => item.evidence_refs),
    ...detail.sources.flatMap(source => source.needs.map(need => ({
      source_id: need.source_id, fragment_id: need.fragment_id, quote: need.evidence_quote,
    })))]
  const unique = refs.filter((ref, index) => refs.findIndex(item => sameValue(item, ref)) === index)
  return unique.map(ref => {
    const source = sources.findLast(item => item.ticket.source_id === ref.source_id)
    return { ref, verified: evidenceVerified(source, ref, detail.topic.passport.product_id) }
  })
}

export function evidenceVerified(source: SourceDetail | undefined, ref: EvidenceRef, productId: string) {
  const document = source?.document
  return Boolean(ref.quote.trim() && source && !source.requires_analysis && document &&
    document.version === source.source_version && document.content_hash === source.content_hash &&
    source.ticket.product_id === productId && document.product_id === productId &&
    document.fragments.some(fragment => fragment.fragment_id === ref.fragment_id && fragment.text.includes(ref.quote)))
}
