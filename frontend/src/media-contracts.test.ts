import { describe, expect, it } from 'vitest'
import { sourceMediaUrl, weeklyReportUrl } from './api'

describe('адреса API передачи У4', () => {
  it('использует same-origin и точную версию вместо имени файла', () => {
    expect(sourceMediaUrl('source 1', 2, 'asset/a')).toBe('/api/v1/sources/source%201/versions/2/assets/asset%2Fa')
    expect(weeklyReportUrl).toBe('/api/v1/reports/weekly')
  })
  it('не разрешает неопределённую или некорректную версию', () => {
    for (const version of [0, -1, 1.5, Number.NaN]) expect(() => sourceMediaUrl('s', version, 'a')).toThrow()
  })
})
