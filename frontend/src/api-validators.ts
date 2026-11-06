import type { SourceDetail, TopicDetail, TopicListItem, TopicStatus } from './api-types'

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}
function isVersion(value: unknown) { return isCount(value) && value >= 1 }
function isNullableString(value: unknown) { return value === null || typeof value === 'string' }
function isDate(value: unknown) { return typeof value === 'string' && Number.isFinite(Date.parse(value)) }
function isNullableDate(value: unknown) { return value === null || isDate(value) }
function isStrings(value: unknown) { return Array.isArray(value) && value.every((item: unknown) => typeof item === 'string') }
function isStatus(value: unknown) {
  const statuses: TopicStatus[] = ['new', 'needs_research', 'linked', 'deferred', 'declined', 'implemented', 'merged']
  return statuses.some(status => status === value)
}
function isEvidence(value: unknown) {
  return isObject(value) && typeof value.source_id === 'string' && typeof value.fragment_id === 'string' && typeof value.quote === 'string'
}
function isExamples(value: unknown) {
  return Array.isArray(value) && value.every((item: unknown) => isObject(item) && typeof item.text === 'string' &&
    Array.isArray(item.evidence_refs) && item.evidence_refs.every(isEvidence))
}

export function isTopicListItem(value: unknown): value is TopicListItem {
  if (!isObject(value) || !isObject(value.topic) || !isObject(value.stats)) return false
  const topic = value.topic
  const stats = value.stats
  return typeof topic.topic_id === 'string' && topic.topic_id.length > 0 &&
    typeof topic.title === 'string' && topic.title.length > 0 && isStatus(topic.status) &&
    isNullableString(topic.owner) && isCount(stats.ticket_count) &&
    (stats.requester_count === null || isCount(stats.requester_count)) &&
    (stats.support_level === 'single' || stats.support_level === 'repeated') &&
    (value.model_mode === 'live' || value.model_mode === 'replay') &&
    (value.analysis_mode === null || value.analysis_mode === 'live' || value.analysis_mode === 'replay') &&
    typeof value.synthetic === 'boolean' && (value.data_origin === 'model' || value.data_origin === 'authored') &&
    isStrings(value.pending_source_ids)
}

export function isSourceDetail(value: unknown): value is SourceDetail {
  if (!isObject(value) || !isObject(value.ticket) || !Array.isArray(value.needs)) return false
  const ticket = value.ticket
  if (!(typeof ticket.source_id === 'string' && typeof ticket.product_id === 'string' &&
    isDate(ticket.created_at) && typeof ticket.text === 'string' && isNullableString(ticket.requester_id) &&
    Array.isArray(ticket.assets) && ticket.assets.every((asset: unknown) => isObject(asset) &&
      typeof asset.asset_id === 'string' && (asset.kind === 'audio' || asset.kind === 'image') &&
      typeof asset.path === 'string' && ['audio/wav', 'audio/mpeg', 'image/png', 'image/jpeg'].some(mime => mime === asset.mime_type) &&
      isNullableString(asset.source_url)))) return false
  if (!(isVersion(value.source_version) && isNullableString(value.content_hash) &&
    typeof value.requires_analysis === 'boolean' && typeof value.synthetic === 'boolean' &&
    (value.data_origin === 'authored' || value.data_origin === 'imported'))) return false
  if (!value.needs.every((need: unknown) => isObject(need) &&
    ['need_id', 'source_id', 'fragment_id', 'evidence_quote', 'scenario', 'statement'].every(field => typeof need[field] === 'string') &&
    ['problem', 'question', 'operational_request', 'feature_request'].some(kind => kind === need.need_type) &&
    typeof need.explicit_request === 'boolean' && isNullableString(need.improvement_hypothesis) && isNullableString(need.uncertainty_reason))) return false
  if (value.document === null) return true
  const document = value.document
  return isObject(document) && document.source_id === ticket.source_id && document.product_id === ticket.product_id &&
    typeof document.content_hash === 'string' && isVersion(document.version) && typeof document.normalization_version === 'string' &&
    Array.isArray(document.fragments) && document.fragments.every((fragment: unknown) => isObject(fragment) &&
      typeof fragment.fragment_id === 'string' && isNullableString(fragment.asset_id) &&
      ['text', 'asr', 'vision'].some(kind => kind === fragment.kind) && typeof fragment.text === 'string' &&
      (fragment.start_ms === null || isCount(fragment.start_ms)) && (fragment.end_ms === null || isCount(fragment.end_ms)) &&
      isNullableString(fragment.warning)) &&
    Array.isArray(document.errors) && document.errors.every((error: unknown) => isObject(error) &&
      error.source_id === ticket.source_id && isNullableString(error.asset_id) &&
      ['stage', 'code', 'message'].every(field => typeof error[field] === 'string'))
}

export function isTopicDetail(value: unknown): value is TopicDetail {
  if (!isTopicListItem(value)) return false
  const detail: unknown = value
  if (!isObject(detail) || !isObject(detail.topic) || !isObject(detail.stats)) return false
  const topic = detail.topic
  const stats = detail.stats
  const passport = topic.passport
  return typeof topic.summary === 'string' && isVersion(topic.version) && isNullableString(topic.canonical_topic_id) &&
    isStrings(topic.need_ids) && Array.isArray(topic.evidence) && topic.evidence.every(isEvidence) &&
    isObject(passport) && ['product_id', 'scenario', 'need_statement', 'scope'].every(field => typeof passport[field] === 'string') &&
    typeof passport.scope_confirmed === 'boolean' && isVersion(passport.version) &&
    isExamples(passport.inclusion_examples) && isExamples(passport.exclusion_examples) &&
    isObject(stats.weekly_counts) && Object.values(stats.weekly_counts).every(isCount) &&
    isNullableDate(stats.first_seen) && isNullableDate(stats.last_seen) &&
    isCount(stats.new_ticket_count_since_decision) && isCount(stats.post_implementation_ticket_count) && typeof stats.review_due === 'boolean' &&
    Array.isArray(detail.sources) && detail.sources.every(isSourceDetail) &&
    Array.isArray(detail.history) && detail.history.every((event: unknown) => isObject(event) &&
      ['event_id', 'topic_id', 'owner', 'reason'].every(field => typeof event[field] === 'string') &&
      isStatus(event.previous_status) && isStatus(event.new_status) && isDate(event.occurred_at) && isVersion(event.topic_version) &&
      isNullableString(event.existing_issue_ref) && isNullableDate(event.review_after) && isNullableDate(event.implemented_at) &&
      isNullableString(event.implementation_comment))
}
