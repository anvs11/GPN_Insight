// Ответы /api/v1/health и /runs: backend/app/schemas.py и /openapi.json.
// Сервер сериализует также поля с default и null; режим клиент не задаёт.
export type ModelMode = 'live' | 'replay'
export type Origin = 'model' | 'authored'

export interface ModelAvailability {
  status: 'available' | 'unavailable' | 'not_checked' | 'not_configured' | 'not_implemented'
  configured: boolean
  checked_at: string | null
  code: string
  message: string
}

export interface Health {
  status: 'ok'
  model_mode: ModelMode
  processing_state: 'idle' | 'busy' | 'not_implemented'
  available_models: Partial<Record<'text' | 'asr' | 'vision', ModelAvailability>>
  replay_origins: Origin[]
  replay_record_count: number
  pipeline_ready: boolean
  integration_errors: string[]
}

export interface ApiErrorBody {
  code: string
  message: string
  run_id?: string | null
}

export interface RunRequest {
  product_id: 'webneft'
  date_from?: string | null
  date_to?: string | null
}

export interface MaterialError {
  source_id: string
  asset_id: string | null
  stage: string
  code: string
  message: string
}

export interface Run {
  run_id: string
  product_id: string
  mode: ModelMode
  state: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'interrupted'
  stage: string
  successful_materials: number
  failed_materials: number
  total_materials: number
  coverage: number | null
  errors: MaterialError[]
  created_at: string
  started_at: string | null
  finished_at: string | null
  origins: Origin[]
  completed_steps: number
  reused_steps: number
  failure_code: string | null
  failure_message: string | null
}

export type TopicStatus = 'new' | 'needs_research' | 'linked' | 'deferred' | 'declined' | 'implemented' | 'merged'

export interface TopicFilters {
  status?: TopicStatus
  support_level?: 'single' | 'repeated' | 'all'
  date_from?: string
  date_to?: string
}

export interface TopicStats {
  ticket_count: number
  requester_count: number | null
  weekly_counts: Record<string, number>
  first_seen: string | null
  last_seen: string | null
  support_level: 'single' | 'repeated'
  new_ticket_count_since_decision: number
  post_implementation_ticket_count: number
  review_due: boolean
}

export interface TopicPeriodStats extends TopicStats {
  date_from: string | null
  date_to: string | null
}

// Проекция TopicDetail из GET /topics: только поля, которые читает список.
// Сервер также возвращает паспорт, источники и историю; это не новый HTTP-формат.
export interface TopicListItem {
  period_stats: TopicPeriodStats | null
  topic: {
    topic_id: string
    title: string
    status: TopicStatus
    owner: string | null
  }
  stats: {
    ticket_count: number
    requester_count: number | null
    support_level: 'single' | 'repeated'
  }
  model_mode: ModelMode
  analysis_mode: ModelMode | null
  synthetic: boolean
  data_origin: Origin
  pending_source_ids: string[]
}

export interface EvidenceRef {
  source_id: string
  fragment_id: string
  quote: string
}

export interface PassportExample {
  text: string
  evidence_refs: EvidenceRef[]
}

export interface TopicPassport {
  product_id: string
  scenario: string
  need_statement: string
  scope: string
  inclusion_examples: PassportExample[]
  exclusion_examples: PassportExample[]
  scope_confirmed: boolean
  version: number
}

export interface MaterialInput {
  asset_id: string
  kind: 'audio' | 'image'
  path: string
  mime_type: 'audio/wav' | 'audio/mpeg' | 'image/png' | 'image/jpeg'
  source_url: string | null
}

export interface Fragment {
  fragment_id: string
  asset_id: string | null
  kind: 'text' | 'asr' | 'vision'
  text: string
  start_ms: number | null
  end_ms: number | null
  warning: string | null
}

export interface NormalizedDocument {
  product_id: string
  source_id: string
  content_hash: string
  version: number
  normalization_version: string
  fragments: Fragment[]
  errors: MaterialError[]
}

export interface ExtractedNeed {
  need_id: string
  source_id: string
  fragment_id: string
  evidence_quote: string
  scenario: string
  need_type: 'problem' | 'question' | 'operational_request' | 'feature_request'
  statement: string
  explicit_request: boolean
  improvement_hypothesis: string | null
  uncertainty_reason: string | null
}

export interface SourceDetail {
  ticket: {
    source_id: string
    product_id: string
    created_at: string
    text: string
    requester_id: string | null
    assets: MaterialInput[]
  }
  document: NormalizedDocument | null
  needs: ExtractedNeed[]
  source_version: number
  content_hash: string | null
  requires_analysis: boolean
  synthetic: boolean
  data_origin: 'authored' | 'imported'
}

export interface DecisionEvent {
  event_id: string
  topic_id: string
  previous_status: TopicStatus
  new_status: TopicStatus
  owner: string
  reason: string
  existing_issue_ref: string | null
  review_after: string | null
  implemented_at: string | null
  implementation_comment: string | null
  occurred_at: string
  topic_version: number
}

// Поля DecisionUpdate, используемые текущей формой. Остальные поля DTO
// необязательны; текущий UI сохраняет только эти два целевых статуса.
export interface DecisionUpdate {
  status: 'new' | 'needs_research'
  owner: string
  reason: string
  version: number
}

export interface TopicDetail extends TopicListItem {
  topic: TopicListItem['topic'] & {
    passport: TopicPassport
    summary: string
    need_ids: string[]
    evidence: EvidenceRef[]
    version: number
    canonical_topic_id: string | null
  }
  stats: TopicStats
  sources: SourceDetail[]
  history: DecisionEvent[]
}
