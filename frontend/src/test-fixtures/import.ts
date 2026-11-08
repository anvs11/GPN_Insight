import type { ImportPreview, ImportResult, ImportLogEntry, ReviewItem } from '../api-types'

export const importPreview: ImportPreview = {
  dataset_id: 'synthetic-frontend-import', status: 'valid', can_commit: true,
  counts: { inserted: 1, updated: 0, unchanged: 0, invalid: 0 }, errors: [],
  warnings: ['Счётчики предварительные. При подтверждении набор проверяется заново.'],
}
export const importResult: ImportResult = {
  dataset_id: 'synthetic-frontend-import', counts: { inserted: 1, updated: 0, unchanged: 0, invalid: 0 },
  imported_at: '2026-11-09T12:00:00+03:00',
}
export const importEntry: ImportLogEntry = {
  registration_id: 'synthetic-registration', product_id: 'webneft', result: importResult, synthetic: true,
}
export const reviewItem: ReviewItem = {
  review_id: 'synthetic-review', version: 1, kind: 'assignment', need_ids: ['synthetic-need'], topic_ids: [],
  candidate_topic_ids: [], reason: 'Синтетический спорный случай, не результат живой модели.',
  evidence_refs: [{ source_id: 'synthetic-source', fragment_id: 'synthetic-fragment', quote: 'Изменить контакт в заявке' }],
  status: 'open', resolution: null, requires_recheck: false,
}
