import { describe, expect, it } from 'vitest'
import { changesPreserved, passportEvidence, validPassportReceipt, verifyPassport } from './passport-verification'
import { passportChange, passportDetail, passportRequest, savedPassport } from './test-fixtures/passport'
import { savedDecision } from './test-fixtures/detail'

const request = passportRequest(), saved = savedPassport(passportDetail, request), event = passportChange(passportDetail, request)

describe('Подтверждение паспорта', () => {
  it('требует обе версии, сохранённые поля, решение и событие before/after', () => {
    expect(validPassportReceipt(passportDetail, request, saved)).toBe(true)
    expect(verifyPassport(passportDetail, request, saved, [], [event], saved)).toBe('confirmed')
    expect(verifyPassport(passportDetail, request, saved, [], [])).toBe('unconfirmed')
    expect(verifyPassport(passportDetail, request, saved, [], [{ ...event, reason: 'Чужая правка' }])).toBe('unconfirmed')
    expect(verifyPassport(passportDetail, request, saved, [], [{ ...event, before: { ...event.before, version: 999 } }])).toBe('unconfirmed')
  })
  it.each(['topic', 'passport', 'decision', 'history', 'counts', 'sources', 'links', 'origin'])('не принимает нарушение %s', field => {
    const value = structuredClone(saved)
    if (field === 'topic') value.topic.version++
    if (field === 'passport') value.topic.passport.version++
    if (field === 'decision') value.topic.status = 'new'
    if (field === 'history') value.history = []
    if (field === 'counts') value.stats.ticket_count++
    if (field === 'sources') value.sources = []
    if (field === 'links') value.topic.need_ids = []
    if (field === 'origin') value.data_origin = 'model'
    expect(validPassportReceipt(passportDetail, request, value)).toBe(false)
  })
  it('сохраняет прежнюю историю изменений и различает более позднее решение', () => {
    const old = { ...event, event_id: 'synthetic-old' }
    expect(verifyPassport(passportDetail, request, saved, [old], [event])).toBe('unconfirmed')
    expect(changesPreserved([old], [{ ...old, reason: 'Перезаписано' }])).toBe(false)
    const later = savedDecision(saved, { status: 'new', version: saved.topic.version, owner: 'Другой', reason: 'Позднее решение' })
    expect(verifyPassport(passportDetail, request, later, [], [event], saved)).toBe('changed')
    later.history[0] = { ...later.history[0], reason: 'История испорчена' }
    expect(verifyPassport(passportDetail, request, later, [], [event], saved)).toBe('unconfirmed')
  })
  it('не считает доказанной цитату из другого фрагмента, pending или старой версии', () => {
    const base = structuredClone(passportDetail)
    expect(passportEvidence(base, [], []).every(option => option.verified)).toBe(true)
    base.sources[0].requires_analysis = true
    expect(passportEvidence(base, [], []).some(option => option.verified)).toBe(false)
    base.sources[0].requires_analysis = false
    base.sources[0].source_version++
    expect(passportEvidence(base, [], []).some(option => option.verified)).toBe(false)
    base.sources[0].source_version--
    base.sources[0].document!.fragments[0].text = 'Другой текст'
    expect(passportEvidence(base, [], []).some(option => option.verified)).toBe(false)
  })
})
