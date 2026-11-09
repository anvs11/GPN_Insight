import { describe, expect, it } from 'vitest'
import { isSourceDetail } from './api-validators'
import { sourceDetail } from './test-fixtures/detail'

describe('Совместимый контракт доступа к оригиналу', () => {
  it('принимает прежний источник и явный restricted без сырого текста', () => {
    expect(isSourceDetail(sourceDetail)).toBe(true)
    expect(isSourceDetail({ ...sourceDetail, synthetic: false, original_access: 'restricted',
      ticket: { ...sourceDetail.ticket, text: '', requester_id: null } })).toBe(true)
  })

  it('не принимает неизвестное разрешение доступа', () => {
    expect(isSourceDetail({ ...sourceDetail, original_access: 'unrestricted-by-client' })).toBe(false)
  })
})
