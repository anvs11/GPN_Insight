import type { DecisionEvent, DecisionUpdate, TopicDetail } from './api-types'

function sameEvent(expected: DecisionEvent, actual: DecisionEvent) {
  return (Object.keys(expected) as (keyof DecisionEvent)[]).every(key => expected[key] === actual[key])
}

function historyPreserved(before: TopicDetail, after: TopicDetail) {
  return before.history.every(event => after.history.some(current => current.event_id === event.event_id && sameEvent(event, current)))
}

function savedEvent(before: TopicDetail, request: DecisionUpdate, after: TopicDetail) {
  return after.history.find(event => event.topic_id === before.topic.topic_id && event.topic_version === request.version + 1 &&
    event.previous_status === before.topic.status && event.new_status === request.status &&
    event.owner === request.owner && event.reason === request.reason && event.existing_issue_ref === (request.existing_issue_ref ?? null) &&
    event.review_after === (request.review_after ?? null) &&
    (event.implemented_at === null && !request.implemented_at || event.implemented_at !== null && !!request.implemented_at &&
      Date.parse(event.implemented_at) === Date.parse(request.implemented_at)) && event.implementation_comment === (request.implementation_comment ?? null))
}

export function verifyDecision(before: TopicDetail, request: DecisionUpdate, current: TopicDetail, receipt?: TopicDetail): 'confirmed' | 'changed' | 'unconfirmed' {
  if (before.topic.topic_id !== current.topic.topic_id || request.version !== before.topic.version || !historyPreserved(before, current)) return 'unconfirmed'
  const event = savedEvent(before, request, current)
  if (!event) return 'unconfirmed'
  if (receipt) {
    const accepted = savedEvent(before, request, receipt)
    if (!accepted || !sameEvent(accepted, event) || !historyPreserved(receipt, current)) return 'unconfirmed'
  }
  if (current.topic.version > event.topic_version) return 'changed'
  return current.topic.version === event.topic_version && current.topic.status === request.status && current.topic.owner === request.owner
    ? 'confirmed' : 'unconfirmed'
}

export function validDecisionReceipt(before: TopicDetail, request: DecisionUpdate, receipt: TopicDetail) {
  return receipt.history.length === before.history.length + 1 && verifyDecision(before, request, receipt) === 'confirmed'
}
