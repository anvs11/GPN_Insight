/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReviewAttempt } from './review-verification'
import { emptyProposal, proposalProblem, reviewEvidence } from './review-create'
import { reviewRequestHash, reviewVersions, verifyReview } from './review-verification'
import { createRequest, proposedTopic, resolvedContext, reviewContext, reviewSource } from './test-fixtures/review'
beforeEach(() => vi.stubGlobal('crypto', webcrypto))
const before = { ...reviewContext, sources: [reviewSource] }
async function saved() {
  const attempt: ReviewAttempt = { before, request: createRequest, hash: await reviewRequestHash('synthetic-review', createRequest) }
  return { attempt, after: resolvedContext(createRequest, attempt.hash, before) }
}
describe('Создание из Review: реальные цитаты и повторное чтение', () => {
  it('имеет пустой самостоятельный черновик без подставленного результата модели', () => {
    const a = emptyProposal(), b = emptyProposal(); a.inclusion_examples[0].text = 'A'
    expect(b.inclusion_examples[0].text).toBe('')
    expect(proposalProblem(b, before.review, before.sources)).toContain('Заполните')
  })
  it('принимает точную цитату текущей потребности Review', () => {
    expect(proposalProblem(proposedTopic, before.review, before.sources)).toBeNull()
    expect(reviewEvidence(before.review, before.sources)[0].verified).toBe(true)
  })
  it.each(['title', 'summary', 'scenario', 'need_statement', 'scope'] as const)('не допускает пустое обязательное поле %s', key => {
    expect(proposalProblem({ ...proposedTopic, [key]: '  ' }, before.review, before.sources)).toContain('Заполните')
  })
  it.each(['requires_analysis', 'fragment', 'quote', 'need', 'version', 'hash', 'product'] as const)('блокирует неподтверждённый источник: %s', kind => {
    const source = structuredClone(reviewSource)
    if (kind === 'requires_analysis') source.requires_analysis = true
    if (kind === 'fragment') source.document!.fragments = []
    if (kind === 'quote') source.document!.fragments[0].text = 'Другая цитата'
    if (kind === 'need') source.needs = []
    if (kind === 'version') source.source_version += 1
    if (kind === 'hash') source.content_hash = 'b'.repeat(64)
    if (kind === 'product') source.ticket.product_id = 'other'
    expect(proposalProblem(proposedTopic, before.review, [source])).toContain('не подтверждены')
  })
  it('требует пример включения и цитаты каждого примера, включая исключения', () => {
    expect(proposalProblem({ ...proposedTopic, inclusion_examples: [] }, before.review, before.sources)).toContain('хотя бы один')
    expect(proposalProblem({ ...proposedTopic, exclusion_examples: [{ text: 'Пример', evidence_refs: [] }] }, before.review, before.sources)).toContain('У каждого')
    const altered = structuredClone(proposedTopic); altered.inclusion_examples[0].evidence_refs[0].quote += ' выдумано'
    expect(proposalProblem(altered, before.review, before.sources)).toContain('У каждого')
  })
  it('включает только версии прежних назначений и не кандидатов', () => {
    expect(reviewVersions(before, 'create', 'synthetic-topic')).toEqual({})
    const linked = structuredClone(before); linked.topics[0].topic.need_ids.push(...before.review.need_ids)
    expect(reviewVersions(linked, 'create', '')).toEqual({ 'synthetic-topic': 3 })
  })
  it('различает команды с разными паспортами по хешу', async () => {
    expect(await reviewRequestHash('synthetic-review', createRequest)).not.toBe(
      await reviewRequestHash('synthetic-review', { ...createRequest, proposed_topic: { ...proposedTopic, summary: 'Другое' } }))
  })
  it('подтверждает паспорт, связь, историю и серверную версию 1 + число потребностей', async () => {
    const { attempt, after } = await saved()
    expect(verifyReview(attempt, after)).toBe('confirmed')
    expect(after.topics.at(-1)!.topic.version).toBe(2)
  })
  it.each(['passport', 'needs', 'evidence', 'origin', 'analysis', 'scope_confirmed', 'history', 'new_version', 'old_history', 'receipt_id', 'count', 'extra_source'] as const)(
    'не подтверждает ошибочный результат: %s', async kind => {
      const { attempt, after } = await saved(); const card = after.topics.at(-1)!
      if (kind === 'passport') card.topic.passport.scope = 'Подменено'
      if (kind === 'needs') card.topic.need_ids = []
      if (kind === 'evidence') card.topic.evidence = []
      if (kind === 'origin') card.data_origin = 'model'
      if (kind === 'analysis') card.analysis_mode = 'replay'
      if (kind === 'scope_confirmed') card.topic.passport.scope_confirmed = true
      if (kind === 'history') after.createdChanges = []
      if (kind === 'new_version') card.topic.version = 1
      if (kind === 'count') card.stats.ticket_count = 2
      if (kind === 'extra_source') {
        const extra = structuredClone(reviewSource); extra.ticket.source_id = 'other-source'
        card.sources.push(extra); card.stats.ticket_count = 2
      }
      if (kind === 'old_history') after.topics[0].history = []
      if (kind === 'receipt_id') attempt.receipt = { ...after.review, resolution: { ...after.review.resolution!, topic_id: 'other' } }
      expect(verifyReview(attempt, after)).toBe('unconfirmed')
    })
  it('проверяет снятие прежних назначений и считает две потребности одного источника одним обращением', async () => {
    const initial = structuredClone(before)
    const second = { ...initial.sources[0].needs[0], need_id: 'synthetic-need-second', evidence_quote: 'контакт в заявке' }
    initial.sources[0].needs.push(second)
    initial.review.need_ids.push(second.need_id)
    initial.review.evidence_refs.push({ source_id: second.source_id, fragment_id: second.fragment_id, quote: second.evidence_quote })
    initial.topics[0].topic.need_ids.push(...initial.review.need_ids)
    const request = { ...createRequest, topic_versions: { 'synthetic-topic': 3 },
      proposed_topic: { ...proposedTopic, inclusion_examples: [{ text: 'Два подтверждения', evidence_refs: initial.review.evidence_refs }] } }
    const a = { before: initial, request, hash: await reviewRequestHash('synthetic-review', request) }
    const after = resolvedContext(request, a.hash, initial)
    after.topics.at(-1)!.sources = initial.sources
    // GET сортирует связи по need_id, история сохраняет исходный порядок команды.
    after.topics.at(-1)!.topic.need_ids = [...after.topics.at(-1)!.topic.need_ids].reverse()
    after.topics.at(-1)!.topic.evidence = [...after.topics.at(-1)!.topic.evidence].reverse()
    expect(verifyReview(a, after)).toBe('confirmed')
    expect(after.topics.at(-1)!.topic.version).toBe(3)
    expect(after.topics.at(-1)!.stats.ticket_count).toBe(1)
    after.topics[0].topic.need_ids.push(second.need_id)
    expect(verifyReview(a, after)).toBe('unconfirmed')
  })
  it('не подтверждает создание, если тема отсутствует в полном каталоге', async () => {
    const { attempt, after } = await saved(); after.topics.pop()
    expect(verifyReview(attempt, after)).toBe('unconfirmed')
  })
})
