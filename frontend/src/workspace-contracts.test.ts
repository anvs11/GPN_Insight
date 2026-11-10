import { describe, expect, it } from 'vitest'
import { isWorkspacePage, isSnapshot } from './workspace-contracts'
import { workspacePage } from './test-fixtures/workspace'

describe('Новые read-контракты рабочего пространства', () => {
  it('принимает настоящий формат, отвергает подменённую ревизию и дубли', () => {
    expect(isWorkspacePage(workspacePage)).toBe(true)
    expect(isWorkspacePage({ ...workspacePage, items: [{ ...workspacePage.items[0], revision: 'fake' }] })).toBe(false)
    expect(isWorkspacePage({ ...workspacePage, items: [workspacePage.items[0], workspacePage.items[0]] })).toBe(false)
    expect(isWorkspacePage({ ...workspacePage, total: 0 })).toBe(false)
    expect(isWorkspacePage({ ...workspacePage, items: [{ ...workspacePage.items[0], priorities: ['guaranteed_critical'] }] })).toBe(false)
  })
  it('не выдаёт неподтверждённый снимок за готовый экспорт', () => {
    expect(isSnapshot({ report_id: 'fake', title: 'fake', markdown: '# Success' })).toBe(false)
  })
})
