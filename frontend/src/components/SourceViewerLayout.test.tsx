import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import type { SourceDetail } from '../api-types'
import { sourceDetail } from '../test-fixtures/detail'
import { processingDetail, processingSource } from '../test-fixtures/processing'
import { SourceViewer } from './SourceViewer'

describe('Структура обращения', () => {
  it('раскрывает и скрывает исходный текст и ASR, технические фрагменты закрыты отдельно', async () => {
    const source: SourceDetail = { ...sourceDetail, document: { ...sourceDetail.document!, fragments: [...sourceDetail.document!.fragments,
      { ...sourceDetail.document!.fragments[0], fragment_id: 'synthetic-asr', kind: 'asr', asset_id: 'synthetic-audio', start_ms: 0, end_ms: 1000, text: 'Расшифрованный синтетический звонок.' }] } }
    vi.spyOn(api, 'source').mockResolvedValue(source)
    render(<SourceViewer sourceId={source.ticket.source_id} onClose={() => {}} />)
    const toggle = await screen.findByRole('button', { name: 'Показать текст обращения и расшифровки' })
    const content = document.getElementById(toggle.getAttribute('aria-controls')!)!
    expect(content.hidden).toBe(true)
    expect(document.querySelector<HTMLDetailsElement>('.source-technical')?.open).toBe(false)
    fireEvent.click(toggle)
    expect(content.hidden).toBe(false)
    expect(within(content).getByText('Расшифрованный синтетический звонок.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Скрыть текст обращения и расшифровки' }))
    expect(content.hidden).toBe(true)
  })

  it('сохраняет потребности и видимые ошибки при недоступной оценке, не теряет сверку цитаты', async () => {
    vi.spyOn(api, 'source').mockResolvedValue(processingSource)
    vi.spyOn(api, 'processing').mockRejectedValue(new ApiError(503, { code: 'DATABASE_UNAVAILABLE', message: 'Оценки временно недоступны.' }))
    const ref = processingDetail.assessments['synthetic-need-1']!.assessment.evidence_refs[0]
    render(<SourceViewer sourceId={processingSource.ticket.source_id} evidence={ref} onClose={() => {}} />)
    await screen.findByText('Оценки временно недоступны.')
    expect(screen.getByRole('heading', { name: processingSource.needs[0].statement })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Ошибки обработки материалов' })).toBeTruthy()
    expect(screen.getByText(/Цитата найдена в указанном фрагменте/)).toBeTruthy()
    expect(screen.getByText(/Оценка отсутствует или не опубликована/)).toBeTruthy()
  })

  it('не дублирует список потребностей после получения оценок и не раскрывает запрещённый оригинал', async () => {
    vi.spyOn(api, 'source').mockResolvedValue({ ...processingSource, synthetic: false, original_access: 'restricted', ticket: { ...processingSource.ticket, text: 'RESTRICTED-ORIGINAL-SYNTHETIC-MARKER' } })
    vi.spyOn(api, 'processing').mockResolvedValue(processingDetail)
    render(<SourceViewer sourceId={processingSource.ticket.source_id} onClose={() => {}} />)
    await screen.findByText('Приоритет: Недостаточно данных')
    expect(screen.getAllByRole('heading', { name: processingSource.needs[0].statement })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Показать текст обращения и расшифровки' }))
    expect(screen.queryByText('RESTRICTED-ORIGINAL-SYNTHETIC-MARKER')).toBeNull()
    expect(screen.getByText(/Оригинал несинтетического обращения не показывается/)).toBeTruthy()
  })
})
