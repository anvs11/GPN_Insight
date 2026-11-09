import type { ProposedTopic, ReviewItem, SourceDetail } from './api-types'
import { evidenceVerified, sameValue } from './passport-verification'

export function emptyProposal(): ProposedTopic {
  return { title: '', summary: '', scenario: '', need_statement: '', scope: '',
    inclusion_examples: [{ text: '', evidence_refs: [] }], exclusion_examples: [] }
}
export function reviewEvidence(review: ReviewItem, sources: SourceDetail[]) {
  return review.evidence_refs.map(ref => {
    const source = sources.find(item => item.ticket.source_id === ref.source_id)
    return { ref, verified: evidenceVerified(source, ref, 'webneft') && Boolean(source?.needs.some(need =>
      review.need_ids.includes(need.need_id) && need.source_id === ref.source_id &&
      need.fragment_id === ref.fragment_id && need.evidence_quote === ref.quote)) }
  })
}
export function proposalProblem(proposal: ProposedTopic, review: ReviewItem, sources: SourceDetail[]) {
  if ([proposal.title, proposal.summary, proposal.scenario, proposal.need_statement, proposal.scope].some(value => !value.trim()))
    return 'Заполните название, краткое описание, сценарий, потребность и границы новой темы.'
  const options = reviewEvidence(review, sources)
  if (!options.length || options.some(option => !option.verified) ||
    review.need_ids.some(id => !sources.some(source => source.needs.some(need => need.need_id === id))))
    return 'Текущие источники всех потребностей Review не подтверждены. Повторите чтение; черновик сохранён.'
  if (!proposal.inclusion_examples.length) return 'Добавьте хотя бы один пример включения.'
  if ([...proposal.inclusion_examples, ...proposal.exclusion_examples].some(example => !example.text.trim() ||
    !example.evidence_refs.length || example.evidence_refs.some(ref => !options.some(option => option.verified && sameValue(option.ref, ref)))))
    return 'У каждого примера должны быть непустой текст и выбранная подтверждённая цитата этого Review.'
  return null
}
