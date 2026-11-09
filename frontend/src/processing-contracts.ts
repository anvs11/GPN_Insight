import type { EvidenceRef, NormalizedDocument, Run, SourceDetail, TopicDetail } from './api-types'
import { isSourceDetail } from './api-validators'

export type Category = 'suspected_defect' | 'usability_confusion' | 'improvement_request' | 'operational_request' | 'unclassified'
export type Priority = 'normal' | 'elevated' | 'potentially_critical' | 'unknown'
export const categoryLabels: Record<Category, string> = {
  suspected_defect: 'Предполагаемая ошибка', usability_confusion: 'Неудобство или непонимание',
  improvement_request: 'Пожелание улучшения', operational_request: 'Операционная просьба', unclassified: 'Неразобранное',
}
export const priorityLabels: Record<Priority, string> = {
  normal: 'Обычный', elevated: 'Повышенный', potentially_critical: 'Потенциально критичный', unknown: 'Недостаточно данных',
}
export interface MaterialCoverage {
  unit: 'materials'; total: number; successful: number; failed: number; empty: number; usable: number; ratio: number | null
}
export interface Assessment {
  need_id: string; analysis_digest: string
  assessment: { category: Category; priority: Priority; reason: string; uncertainties: string[]; evidence_refs: EvidenceRef[] }
  metadata: { origin: 'authored' | 'model'; model_id: string; recording_set_version: string }
}
export interface ProcessingDetail {
  format_version: 'partial-source-v1'; current_head_revision: string | null; is_current: boolean
  analysis_pending: boolean | null; manual_recheck_required: boolean
  result: { revision_id: string; analysis_digest: string; state: 'complete' | 'partial' | 'failed' | 'empty'
    context: { product_id: string; source_id: string; source_version: number; content_hash: string; synthetic: boolean; data_origin: string }
    document: NormalizedDocument; coverage: MaterialCoverage }
  assessments: Record<string, Assessment | null>; unassessed_need_ids: string[]
}
export interface AssessmentSummary {
  format_version: 'topic-assessment-summary-v1'; topic_id: string; topic_version: number; unit: 'sources'
  source_count: number; unassessed_source_count: number; categories: Partial<Record<Category, number>>; priorities: Partial<Record<Priority, number>>
}
export interface CompletedWeek {
  timezone: 'Europe/Moscow'; period: { product_id: 'webneft'; date_from: string; date_to: string }
  start_utc: string; end_utc_exclusive: string
}

const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const sha = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v)
const text = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text)

export function isMaterialCoverage(v: unknown): v is MaterialCoverage {
  if (!object(v) || v.unit !== 'materials' || !count(v.total) || !count(v.successful) || !count(v.failed) || !count(v.empty) || !count(v.usable)) return false
  return v.total === v.successful + v.failed && v.successful === v.empty + v.usable &&
    (v.total === 0 ? v.ratio === null : typeof v.ratio === 'number' && Math.abs(v.ratio - v.successful / v.total) < 1e-12)
}

export function isProcessingDetail(v: unknown, source: SourceDetail): v is ProcessingDetail {
  if (!object(v) || v.format_version !== 'partial-source-v1' || v.is_current !== true ||
    typeof v.analysis_pending !== 'boolean' || typeof v.manual_recheck_required !== 'boolean' || !object(v.result) || !object(v.result.context)) return false
  const r = v.result, c = v.result.context
  if (c.source_id !== source.ticket.source_id || c.product_id !== source.ticket.product_id || c.source_version !== source.source_version ||
    c.content_hash !== source.content_hash || c.synthetic !== source.synthetic || c.data_origin !== source.data_origin ||
    !sha(r.revision_id) || v.current_head_revision !== r.revision_id ||
    source.current_result_revision !== r.revision_id || !sha(r.analysis_digest) || !isMaterialCoverage(r.coverage) || !object(r.document) ||
    r.document.source_id !== c.source_id || r.document.version !== c.source_version || r.document.content_hash !== c.content_hash ||
    !Array.isArray(r.document.fragments) || !Array.isArray(r.document.errors) || r.document.errors.length !== r.coverage.failed ||
    !['complete', 'partial', 'failed', 'empty'].some(state => state === r.state) ||
    !object(v.assessments) || !Array.isArray(v.unassessed_need_ids) || !v.unassessed_need_ids.every(text) ||
    !isSourceDetail({ ...source, document: r.document })) return false
  const fragments = r.document.fragments
  const expectedState = r.coverage.failed ? (r.coverage.successful ? 'partial' : 'failed') : r.coverage.usable ? 'complete' : 'empty'
  if (r.state !== expectedState) return false
  if (!fragments.every(f => object(f) && text(f.fragment_id) && typeof f.text === 'string') ||
    new Set(fragments.map(f => f.fragment_id)).size !== fragments.length) return false
  const needIds = new Set(source.needs.map(n => n.need_id))
  for (const [id, value] of Object.entries(v.assessments)) {
    if (!needIds.has(id)) return false
    if (value === null) continue
    if (!object(value) || value.need_id !== id || value.analysis_digest !== r.analysis_digest || !object(value.assessment) || !object(value.metadata)) return false
    const a = value.assessment, meta = value.metadata
    if (typeof a.category !== 'string' || !Object.hasOwn(categoryLabels, a.category) || typeof a.priority !== 'string' || !Object.hasOwn(priorityLabels, a.priority) ||
      !text(a.reason) || !Array.isArray(a.uncertainties) || !a.uncertainties.every(text) ||
      (['unknown', 'potentially_critical'].includes(a.priority) && !strings(a.uncertainties)) ||
      (['unknown', 'potentially_critical'].includes(a.priority) && a.uncertainties.length === 0) ||
      !Array.isArray(a.evidence_refs) || a.evidence_refs.length === 0 ||
      !['authored', 'model'].some(origin => origin === meta.origin) || !text(meta.model_id) || !text(meta.recording_set_version)) return false
    if (!a.evidence_refs.every(ref => object(ref) && ref.source_id === c.source_id && text(ref.quote) &&
      fragments.some(f => f.fragment_id === ref.fragment_id && f.text.includes(ref.quote)))) return false
  }
  const missing = Object.entries(v.assessments).filter(([, a]) => a === null).map(([id]) => id).sort()
  return JSON.stringify(missing) === JSON.stringify([...v.unassessed_need_ids].sort())
}

export function hasValidRunCoverage(run: Run): boolean {
  if (run.processing_policy === undefined || run.processing_policy === null) return true
  if (run.processing_policy !== 'partial-media-v1' || !isMaterialCoverage(run.material_coverage) || !object(run.source_coverage)) return false
  const c = run.source_coverage
  return c.unit === 'sources' && count(c.total) && count(c.complete) && count(c.partial) && count(c.failed) && count(c.empty) &&
    c.total === c.complete + c.partial + c.failed + c.empty && run.material_coverage.total <= run.total_materials &&
    run.material_coverage.successful === run.successful_materials && run.material_coverage.failed === run.failed_materials
}

export function isAssessmentSummary(v: unknown, card: TopicDetail): v is AssessmentSummary {
  if (!object(v) || v.format_version !== 'topic-assessment-summary-v1' || v.topic_id !== card.topic.topic_id ||
    v.topic_version !== card.topic.version || v.unit !== 'sources' || v.source_count !== card.stats.ticket_count ||
    !count(v.unassessed_source_count) || v.unassessed_source_count > card.stats.ticket_count || !object(v.categories) || !object(v.priorities)) return false
  return Object.entries(v.categories).every(([key, n]) => Object.hasOwn(categoryLabels, key) && count(n) && n <= card.stats.ticket_count) &&
    Object.entries(v.priorities).every(([key, n]) => Object.hasOwn(priorityLabels, key) && count(n) && n <= card.stats.ticket_count)
}

export function isCompletedWeek(v: unknown): v is CompletedWeek {
  if (!object(v) || v.timezone !== 'Europe/Moscow' || !object(v.period) || v.period.product_id !== 'webneft' ||
    !text(v.period.date_from) || !text(v.period.date_to) || !/^\d{4}-\d{2}-\d{2}$/.test(v.period.date_from) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(v.period.date_to) || !text(v.start_utc) || !text(v.end_utc_exclusive)) return false
  const start = Date.parse(v.period.date_from + 'T00:00:00+03:00'), end = Date.parse(v.period.date_to + 'T00:00:00+03:00') + 86400000
  return Number.isFinite(start) && Number.isFinite(end) && end - start === 7 * 86400000 &&
    new Date(v.period.date_from).getUTCDay() === 1 && Date.parse(v.start_utc) === start && Date.parse(v.end_utc_exclusive) === end
}
