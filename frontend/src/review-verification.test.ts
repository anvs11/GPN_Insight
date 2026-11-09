/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { contextKey, historyPreserved, reviewReadConsistent, reviewRequestHash, reviewVersions, verifyReview } from './review-verification'
import type { ReviewAttempt } from './review-verification'
import { attachRequest, resolvedContext, reviewContext, reviewTarget } from './test-fixtures/review'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })
async function attempt(): Promise<ReviewAttempt> {
  return { before: reviewContext, request: attachRequest, hash: await reviewRequestHash(reviewContext.review.review_id, attachRequest) }
}
describe('Подтверждение решения Review', () => {
  it('совпадает с реальным HumanWorkflow._command_hash, включая серверные null defaults', async () => {
    expect(await reviewRequestHash('synthetic-review', attachRequest)).toBe('38fd0d76ecce241d775a3323c4cc5b9a94ef64fc71e63797b1d62faaa473fcda')
  })
  it('хеш insufficient_data совпадает с Backend при пустом topic_versions', async () => {
    const body = { version: 1, action: 'insufficient_data' as const, author: attachRequest.author, reason: attachRequest.reason, topic_versions: {} }
    expect(await reviewRequestHash('synthetic-review', body)).toBe('1878ff98005dc4905d8be13db0676316e172ad3d2bb8406236756e2a8ae28107')
  })
  it('канонический порядок Unicode ключей совпадает с Python, а не порядком UTF-16', async () => {
    const body = { ...attachRequest, topic_versions: { [String.fromCodePoint(0x10000)]: 2, [String.fromCodePoint(0xe000)]: 3 } }
    expect(await reviewRequestHash('synthetic-review', body)).toBe('8ff44cb2d119fe3a723b3d8601c577349ecaafe30bc9dab50ec0d7b0d7aa2d6b')
  })
  it('порядок topic_versions не меняет хеш команды', async () => {
    const a = { ...attachRequest, topic_versions: { b: 3, a: 4 } }, b = { ...attachRequest, topic_versions: { a: 4, b: 3 } }
    expect(await reviewRequestHash('synthetic-review', a)).toBe(await reviewRequestHash('synthetic-review', b))
  })
  it('проверяет потерянный ответ только GET и историей исходной команды', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    expect(verifyReview(a, after)).toBe('confirmed')
  })
  it('не считает чужое решение с тем же автором и причиной своей командой', async () => {
    const a = await attempt()
    expect(verifyReview(a, resolvedContext(a.request, 'f'.repeat(64)))).toBe('unconfirmed')
  })
  it('не подтверждает запись без прежней истории', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    after.history.shift()
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('не подтверждает расходящиеся Review и историю', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    after.review = { ...after.review, version: 3 }
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('не подтверждает attach без фактических связей и версии темы', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    after.topics = [reviewTarget]
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('не подтверждает повреждённую квитанцию даже при успешном HTTP', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    a.receipt = { ...after.review, resolution: { ...after.review.resolution!, reason: 'Другая причина' } }
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('отмечает последующее изменение источника, сохраняя доказательство прежнего решения', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    after.review = { ...after.review, requires_recheck: true }
    expect(verifyReview(a, after)).toBe('changed')
    expect(reviewReadConsistent(after.review, after.history)).toBe(true)
  })
  it('не выдаёт последующее изменение версии темы за текущий эффект нашей команды', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    after.topics[0].topic.version += 1
    expect(verifyReview(a, after)).toBe('changed')
  })
  it('не скрывает исчезновение прежнего бизнес-решения из истории темы', async () => {
    const a = await attempt(), after = resolvedContext(a.request, a.hash)
    a.before = structuredClone(a.before)
    expect(a.before.topics[0].history.length).toBeGreaterThan(0)
    after.topics[0].history = []
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('подтверждает insufficient_data без фиктивной целевой темы', async () => {
    const body = { version: 1, action: 'insufficient_data' as const, author: attachRequest.author, reason: attachRequest.reason, topic_versions: {} }
    const a = { before: reviewContext, request: body, hash: await reviewRequestHash('synthetic-review', body) }
    expect(verifyReview(a, resolvedContext(body, a.hash))).toBe('confirmed')
  })
  it('для обоих действий включает прежние назначения, но не candidate_topic_ids', () => {
    const linked = structuredClone(reviewTarget); linked.topic.topic_id = 'old-topic'
    linked.topic.need_ids = [...linked.topic.need_ids, ...reviewContext.review.need_ids]
    const context = { ...reviewContext, topics: [linked, reviewTarget] }
    expect(reviewVersions(context, 'attach', 'synthetic-topic')).toEqual({ 'old-topic': 3, 'synthetic-topic': 3 })
    expect(reviewVersions(context, 'insufficient_data', '')).toEqual({ 'old-topic': 3 })
    expect(contextKey(context)).not.toBe(contextKey(reviewContext))
  })
  it('проверяет снятие прежних назначений при недостаточности данных', async () => {
    const before = structuredClone(reviewContext)
    before.topics[0].topic.need_ids.push(...before.review.need_ids)
    const request = { version: 1, action: 'insufficient_data' as const, author: 'Автор', reason: 'Причина', topic_versions: { 'synthetic-topic': 3 } }
    const a = { before, request, hash: await reviewRequestHash('synthetic-review', request) }
    const after = resolvedContext(request, a.hash, before)
    expect(verifyReview(a, after)).toBe('confirmed')
    after.topics[0].topic.need_ids.push(...before.review.need_ids)
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('не заменяет append-only историю сравнением только её длины', () => {
    expect(historyPreserved(reviewContext.history, [{ ...reviewContext.review, reason: 'Подменённая история' }])).toBe(false)
  })
})
