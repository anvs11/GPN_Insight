import { sourceDetail, topicDetail } from './detail'
import type { SourceDetail } from '../api-types'
import type { ProcessingDetail, AssessmentSummary, CompletedWeek } from '../processing-contracts'

// Authored/synthetic fixtures только для тестов; runtime их не импортирует.
export const processingSource: SourceDetail = { ...sourceDetail, current_result_revision: 'b'.repeat(64), processing_state: 'partial',
  document: { ...sourceDetail.document!, errors: [{ source_id: sourceDetail.ticket.source_id, asset_id: 'synthetic-audio',
    stage: 'asr', code: 'REPLAY_RECORD_MISSING', message: 'Точной записи нет.' }] } }
export const processingDetail: ProcessingDetail = {
  format_version: 'partial-source-v1', current_head_revision: 'b'.repeat(64), is_current: true, analysis_pending: false, manual_recheck_required: false,
  result: { revision_id: 'b'.repeat(64), analysis_digest: 'c'.repeat(64), state: 'partial',
    context: { product_id: 'webneft', source_id: processingSource.ticket.source_id, source_version: 1,
      content_hash: 'a'.repeat(64), synthetic: true, data_origin: 'authored' }, document: processingSource.document!,
    coverage: { unit: 'materials', total: 2, successful: 1, failed: 1, empty: 0, usable: 1, ratio: .5 } },
  assessments: { 'synthetic-need-1': { need_id: 'synthetic-need-1', analysis_digest: 'c'.repeat(64),
    assessment: { category: 'improvement_request', priority: 'unknown', reason: 'Синтетическая оценка, срочность неизвестна.',
      uncertainties: ['Не установлено влияние на работу.'], evidence_refs: [{ source_id: processingSource.ticket.source_id,
        fragment_id: 'text-1', quote: 'Покажите контактный телефон' }] },
    metadata: { origin: 'authored', model_id: 'synthetic-no-live-model', recording_set_version: 'synthetic-v1' } } },
  unassessed_need_ids: [],
}
export const assessmentSummary: AssessmentSummary = { format_version: 'topic-assessment-summary-v1', topic_id: topicDetail.topic.topic_id,
  topic_version: topicDetail.topic.version, unit: 'sources', source_count: topicDetail.stats.ticket_count, unassessed_source_count: 1,
  categories: { improvement_request: 3 }, priorities: { unknown: 3 } }
export const completedWeek: CompletedWeek = { timezone: 'Europe/Moscow', period: { product_id: 'webneft', date_from: '2026-11-02', date_to: '2026-11-08' },
  start_utc: '2026-11-01T21:00:00Z', end_utc_exclusive: '2026-11-08T21:00:00Z' }
export const weeklyText = 'Неделя: 2026-11-02 — 2026-11-08.\n# Синтетический отчёт\n<script>doNotRun()</script>\norigin=authored; replay.'
