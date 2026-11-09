import { describe, expect, it } from 'vitest'
import { hasValidRunCoverage, isAssessmentSummary, isCompletedWeek, isProcessingDetail } from './processing-contracts'
import type { Run } from './api-types'
import { assessmentSummary, completedWeek, processingDetail, processingSource } from './test-fixtures/processing'
import { topicDetail } from './test-fixtures/detail'

describe('Версии оценок/покрытия и серверная неделя', () => {
  it('принимает связанный partial результат и отличает unknown от отсутствия оценки', () => {
    expect(isProcessingDetail(processingDetail, processingSource)).toBe(true)
    expect(isProcessingDetail({ ...processingDetail, assessments: { 'synthetic-need-1': null }, unassessed_need_ids: ['synthetic-need-1'] }, processingSource)).toBe(true)
  })
  it('отвергает чужую ревизию, ошибочное покрытие, цитату или скрытую ошибку', () => {
    expect(isProcessingDetail(processingDetail, { ...processingSource, current_result_revision: 'd'.repeat(64) })).toBe(false)
    const wrongCoverage = structuredClone(processingDetail)
    wrongCoverage.result.coverage.failed = 0
    expect(isProcessingDetail(wrongCoverage, processingSource)).toBe(false)
    const wrongQuote = structuredClone(processingDetail)
    wrongQuote.assessments['synthetic-need-1']!.assessment.evidence_refs[0].quote = 'Выдуманная цитата'
    expect(isProcessingDetail(wrongQuote, processingSource)).toBe(false)
    const hiddenError = structuredClone(processingDetail)
    hiddenError.result.document.errors = []
    expect(isProcessingDetail(hiddenError, processingSource)).toBe(false)
  })
  it('не смешивает summary другой версии и счётчики больше числа источников', () => {
    expect(isAssessmentSummary(assessmentSummary, topicDetail)).toBe(true)
    expect(isAssessmentSummary({ ...assessmentSummary, topic_version: 999 }, topicDetail)).toBe(false)
    expect(isAssessmentSummary({ ...assessmentSummary, priorities: { normal: 999 } }, topicDetail)).toBe(false)
  })
  it('проверяет MSK границы и не принимает скользящий/неполный период', () => {
    expect(isCompletedWeek(completedWeek)).toBe(true)
    expect(isCompletedWeek({ ...completedWeek, timezone: 'UTC' })).toBe(false)
    expect(isCompletedWeek({ ...completedWeek, end_utc_exclusive: '2026-11-09T00:00:00Z' })).toBe(false)
  })
  it('различает материалы и источники в новых счётчиках запуска', () => {
    const run: Run = { run_id: 'synthetic', product_id: 'webneft', mode: 'replay', state: 'completed_with_errors', stage: 'finished',
      total_materials: 2, successful_materials: 1, failed_materials: 1, coverage: .5, errors: [], origins: ['authored'],
      created_at: '2026-11-09T12:00:00Z', started_at: null, finished_at: null, completed_steps: 1, reused_steps: 0,
      failure_code: null, failure_message: null, processing_policy: 'partial-media-v1',
      material_coverage: processingDetail.result.coverage, source_coverage: { unit: 'sources', total: 1, complete: 0, partial: 1, failed: 0, empty: 0 } }
    expect(hasValidRunCoverage(run)).toBe(true)
    expect(hasValidRunCoverage({ ...run, successful_materials: 2 })).toBe(false)
    expect(hasValidRunCoverage({ ...run, source_coverage: { ...run.source_coverage!, total: 2 } })).toBe(false)
  })
})
