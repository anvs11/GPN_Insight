import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api, ApiError } from '../api'
import type { SourceDetail, TopicDetail as Detail } from '../api-types'
import { sourceDetail, topicDetail } from '../test-fixtures/detail'
import { TopicList } from './TopicList'
import { TopicDetail } from './TopicDetail'
import { SourceViewer } from './SourceViewer'
import { workspacePage, emptyArchive } from '../test-fixtures/workspace'

describe('Карточка темы и подтверждающие источники', () => {
  it('открывает карточку из списка, читает отдельный источник и сохраняет историю только для просмотра', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topics').mockResolvedValue([topicDetail])
    const topic = vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    const source = vi.spyOn(api, 'source').mockResolvedValue(sourceDetail)
    render(<TopicList />)
    await user.click(await screen.findByRole('button', { name: topicDetail.topic.title }))
    const card = within(await screen.findByRole('region', { name: 'Карточка темы' }))
    await card.findByText(topicDetail.topic.passport.need_statement)
    expect(topic).toHaveBeenCalledWith(topicDetail.topic.topic_id, expect.any(AbortSignal))
    expect(card.getByText('Синтетические демонстрационные данные — не статистика компании.')).toBeTruthy()
    expect(card.getByText('Версия темы').nextElementSibling?.textContent).toBe('2')
    expect(card.getByText('Уникальные заявители').nextElementSibling?.textContent).toBe('Нет данных')
    expect(card.getByText('Обращения за всё время').nextElementSibling?.textContent).toBe('4')
    expect(card.getByText(/2026-10-26: 4/)).toBeTruthy()
    expect(card.getByText(/Результат модельного анализа не подтверждён/)).toBeTruthy()
    expect(source).not.toHaveBeenCalled()
    await user.click(card.getByRole('button', { name: 'Проверить источник SYNTHETIC-001' }))
    const viewer = within(await screen.findByRole('region', { name: 'Источник SYNTHETIC-001' }))
    await viewer.findByText(/Цитата найдена в указанном фрагменте/)
    expect(source).toHaveBeenCalledWith('SYNTHETIC-001', expect.any(AbortSignal))
    expect(viewer.getByText(/Синтетический демонстрационный источник/)).toBeTruthy()
    await user.click(card.getByText('История решений (1)'))
    expect(card.getByText('Причина: Сохранённая причина решения')).toBeTruthy()
    expect(card.getByRole('region', { name: 'Решение владельца продукта' })).toBeTruthy()
    await user.click(viewer.getByRole('button', { name: 'Закрыть источник' }))
    expect(screen.queryByRole('region', { name: 'Источник SYNTHETIC-001' })).toBeNull()
    await user.click(card.getByRole('button', { name: 'Закрыть карточку' }))
    expect(screen.queryByRole('region', { name: 'Карточка темы' })).toBeNull()
    expect(screen.getByRole('button', { name: topicDetail.topic.title }).getAttribute('aria-expanded')).toBe('false')
  })

  it('различает загрузку и 404 карточки, сохраняя список и поля панели обработки', async () => {
    const user = userEvent.setup()
    sessionStorage.setItem('user-preference', 'keep')
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', model_mode: 'live', processing_state: 'idle',
      pipeline_ready: false, integration_errors: ['ANALYSIS_NOT_CONNECTED'], available_models: {}, replay_origins: [], replay_record_count: 0 })
    vi.spyOn(api, 'monitoring').mockResolvedValue(workspacePage)
    vi.spyOn(api, 'reportArchive').mockResolvedValue(emptyArchive)
    let reject: (error: ApiError) => void = () => { throw new Error('Запрос не создан') }
    vi.spyOn(api, 'topic').mockReturnValue(new Promise((_resolve, fail) => { reject = fail }))
    render(<App />)
    await screen.findByRole('button', { name: topicDetail.topic.title })
    fireEvent.change(within(screen.getByRole('form', { name: 'Фильтры мониторинга' })).getByLabelText('С даты'), { target: { value: '2026-10-01' } })
    await user.click(screen.getByRole('button', { name: topicDetail.topic.title }))
    expect(screen.getByText('Загружаем карточку…')).toBeTruthy()
    await act(async () => reject(new ApiError(404, { code: 'TOPIC_NOT_FOUND', message: 'Тема отсутствует.' })))
    await screen.findByText('HTTP 404 · TOPIC_NOT_FOUND')
    expect(screen.getByRole('region', { name: 'Темы мониторинга' })).toBeTruthy()
    expect(within(screen.getByRole('form', { name: 'Фильтры мониторинга' })).getByLabelText('С даты')).toHaveProperty('value', '2026-10-01')
    expect(sessionStorage.getItem('user-preference')).toBe('keep')
    expect(screen.getByRole('dialog', { name: 'Тема и решение' })).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Карточка темы' })).queryByText(topicDetail.topic.summary)).toBeNull()
  })

  it('не теряет прочитанную карточку при сетевой ошибке обновления и позволяет повторить GET', async () => {
    const user = userEvent.setup()
    const topic = vi.spyOn(api, 'topic').mockResolvedValueOnce(topicDetail)
      .mockRejectedValueOnce(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Связь потеряна.' }))
      .mockResolvedValue(topicDetail)
    render(<TopicDetail topicId={topicDetail.topic.topic_id} onClose={() => {}} />)
    await screen.findByText(topicDetail.topic.summary)
    await user.click(screen.getByRole('button', { name: 'Обновить карточку' }))
    await screen.findByText('Связь потеряна.')
    expect(screen.getByText(topicDetail.topic.summary)).toBeTruthy()
    expect(screen.getByText(/Показана последняя полученная карточка/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Обновить карточку' }))
    await screen.findByText(topicDetail.topic.summary)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(topic).toHaveBeenCalledTimes(3)
  })

  it('отменяет прежнюю карточку при выборе другой темы и игнорирует запоздалый ответ', async () => {
    const user = userEvent.setup()
    const second = { ...topicDetail, topic: { ...topicDetail.topic, topic_id: 'synthetic-topic-2', title: 'Вторая синтетическая тема' } }
    vi.spyOn(api, 'topics').mockResolvedValue([topicDetail, second])
    let finish: (detail: Detail) => void = () => { throw new Error('Запрос не создан') }
    const topic = vi.spyOn(api, 'topic').mockReturnValueOnce(new Promise(resolve => { finish = resolve })).mockResolvedValue(second)
    render(<TopicList />)
    await user.click(await screen.findByRole('button', { name: topicDetail.topic.title }))
    await user.click(screen.getByRole('button', { name: second.topic.title }))
    const card = within(await screen.findByRole('region', { name: 'Карточка темы' }))
    await card.findByText(second.topic.title)
    expect(topic.mock.calls[0][1]?.aborted).toBe(true)
    await act(async () => finish(topicDetail))
    expect(card.getByText(second.topic.title)).toBeTruthy()
    expect(card.queryByText(topicDetail.topic.title)).toBeNull()
  })

  it('при 404 источника сохраняет карточку и не подменяет ответ встроенной копией обращения', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'topic').mockResolvedValue(topicDetail)
    vi.spyOn(api, 'source').mockRejectedValue(new ApiError(404, { code: 'SOURCE_NOT_FOUND', message: 'Источник отсутствует.' }))
    render(<TopicDetail topicId={topicDetail.topic.topic_id} onClose={() => {}} />)
    await user.click(await screen.findByRole('button', { name: 'Открыть обращение SYNTHETIC-001' }))
    await screen.findByText('HTTP 404 · SOURCE_NOT_FOUND')
    expect(screen.getByText(topicDetail.topic.summary)).toBeTruthy()
    expect(screen.queryByText(sourceDetail.ticket.text)).toBeNull()
  })

  it('показывает отсутствие данных без фиктивных показателей или доказательств', async () => {
    const empty = { ...topicDetail, sources: [], history: [], topic: { ...topicDetail.topic, evidence: [] },
      stats: { ...topicDetail.stats, weekly_counts: {}, first_seen: null, last_seen: null }, pending_source_ids: ['SYNTHETIC-001'] }
    vi.spyOn(api, 'topic').mockResolvedValue(empty)
    render(<TopicDetail topicId={topicDetail.topic.topic_id} onClose={() => {}} />)
    await screen.findByText('Сведения об источниках отсутствуют.')
    expect(screen.getByText('Подтверждающие цитаты отсутствуют.')).toBeTruthy()
    expect(screen.getByText('Данных по неделям нет.')).toBeTruthy()
    expect(screen.getByText(/Ожидают повторного анализа: SYNTHETIC-001/)).toBeTruthy()
  })

  it.each(['live', 'replay'] as const)('явно показывает режим анализа %s и происхождение результата', async mode => {
    vi.spyOn(api, 'topic').mockResolvedValue({ ...topicDetail, model_mode: 'live', analysis_mode: mode, data_origin: 'model' })
    render(<TopicDetail topicId={topicDetail.topic.topic_id} onClose={() => {}} />)
    await screen.findByText(new RegExp(`Режим анализа: ${mode}`))
    expect(screen.getByText(/ответ модели \(model\)/)).toBeTruthy()
    expect(screen.getByText('live')).toBeTruthy()
    if (mode === 'replay') expect(screen.getByText(/сохранённые ответы/)).toBeTruthy()
  })

  it('при выборе другого источника отменяет прежнее чтение и не показывает его поздний ответ', async () => {
    const user = userEvent.setup()
    const second = { ...sourceDetail, ticket: { ...sourceDetail.ticket, source_id: 'SYNTHETIC-002', text: 'Второе синтетическое обращение' }, document: null, needs: [] }
    vi.spyOn(api, 'topic').mockResolvedValue({ ...topicDetail, sources: [sourceDetail, second] })
    let finish: (source: SourceDetail) => void = () => { throw new Error('Запрос не создан') }
    const source = vi.spyOn(api, 'source').mockReturnValueOnce(new Promise(resolve => { finish = resolve })).mockResolvedValue(second)
    render(<TopicDetail topicId={topicDetail.topic.topic_id} onClose={() => {}} />)
    await user.click(await screen.findByRole('button', { name: 'Открыть обращение SYNTHETIC-001' }))
    expect(screen.getByText('Загружаем источник…')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Открыть обращение SYNTHETIC-002' }))
    await screen.findByText(second.ticket.text)
    expect(source.mock.calls[0][1]?.aborted).toBe(true)
    await act(async () => finish(sourceDetail))
    expect(screen.queryByRole('region', { name: 'Источник SYNTHETIC-001' })).toBeNull()
    expect(screen.getByText(second.ticket.text)).toBeTruthy()
  })

  it('отличает необработанный источник от отсутствия потребностей и не подтверждает утраченную цитату', async () => {
    vi.spyOn(api, 'source').mockResolvedValue({ ...sourceDetail, document: null, needs: [], source_version: 2, requires_analysis: true })
    render(<SourceViewer sourceId="SYNTHETIC-001" evidence={topicDetail.topic.evidence[0]} onClose={() => {}} />)
    await screen.findByText(/Подготовленного документа пока нет/)
    expect(screen.getByText(/Источник ожидает анализа/)).toBeTruthy()
    expect(screen.getByText(/Цитата не подтверждена текущим фрагментом/)).toBeTruthy()
    expect(screen.getByText(/Сохранённых потребностей нет. Это не подтверждает/)).toBeTruthy()
  })

  it('показывает ошибки распознавания и только серверные временные метки, без ссылок на файлы', async () => {
    const value: SourceDetail = { ...sourceDetail, ticket: { ...sourceDetail.ticket, text: '', assets: [{ asset_id: 'synthetic-audio',
      kind: 'audio', path: 'synthetic.mp3', mime_type: 'audio/mpeg', source_url: 'https://example.invalid/private' }] },
      document: { ...sourceDetail.document!, fragments: [{ fragment_id: 'asr-1', asset_id: 'synthetic-audio', kind: 'asr',
        text: 'Распознанный фрагмент', start_ms: 1000, end_ms: 2500, warning: 'Распознано частично' }],
      errors: [{ source_id: 'SYNTHETIC-001', asset_id: 'synthetic-audio', stage: 'asr', code: 'ASR_PARTIAL', message: 'Часть записи не распознана.' }] } }
    vi.spyOn(api, 'source').mockResolvedValue(value)
    render(<SourceViewer sourceId="SYNTHETIC-001" onClose={() => {}} />)
    await screen.findByText('Метки сервера (мс): 1000 — 2500')
    expect(screen.getByText(/ASR_PARTIAL/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Открыть медиа synthetic-audio' })).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('не раскрывает оригинал несинтетического источника и не исполняет HTML из нормализованного текста', async () => {
    const markup = '<img src=x onerror="alert(1)">'
    vi.spyOn(api, 'source').mockResolvedValue({ ...sourceDetail, synthetic: false,
      ticket: { ...sourceDetail.ticket, text: 'Оригинал, который нельзя показывать' },
      document: { ...sourceDetail.document!, fragments: [{ ...sourceDetail.document!.fragments[0], text: markup }] } })
    render(<SourceViewer sourceId="SYNTHETIC-001" onClose={() => {}} />)
    await screen.findByText(markup)
    expect(screen.queryByText('Оригинал, который нельзя показывать')).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('сохраняет источник при ошибке повторного чтения и отменяет запрос при закрытии', async () => {
    const user = userEvent.setup()
    const source = vi.spyOn(api, 'source').mockResolvedValueOnce(sourceDetail)
      .mockRejectedValue(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Источник временно недоступен.' }))
    const view = render(<SourceViewer sourceId="SYNTHETIC-001" onClose={() => {}} />)
    await screen.findByText('Версия источника')
    await user.click(screen.getByRole('button', { name: 'Обновить источник' }))
    await screen.findByText('Источник временно недоступен.')
    expect(screen.getByText(/Показан последний полученный источник/)).toBeTruthy()
    expect(screen.getByText('Версия источника')).toBeTruthy()
    view.unmount()
    expect(source.mock.calls[1][1]?.aborted).toBe(true)
  })

  it('не подтверждает цитату по устаревшему источнику во время обновления и после его отказа', async () => {
    const user = userEvent.setup()
    let reject: (error: ApiError) => void = () => { throw new Error('Запрос не создан') }
    vi.spyOn(api, 'source').mockResolvedValueOnce(sourceDetail)
      .mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail }))
      .mockResolvedValue(sourceDetail)
    render(<SourceViewer sourceId="SYNTHETIC-001" evidence={topicDetail.topic.evidence[0]} onClose={() => {}} />)
    await screen.findByText(/Цитата найдена в указанном фрагменте текущего источника/)
    await user.click(screen.getByRole('button', { name: 'Обновить источник' }))
    expect(screen.getByText('Загружаем источник…')).toBeTruthy()
    expect(screen.queryByText(/Цитата найдена в указанном фрагменте текущего источника/)).toBeNull()
    expect(screen.getByText(/Сверка цитаты с текущей версией источника не подтверждена/)).toBeTruthy()
    await act(async () => reject(new ApiError(0, { code: 'NETWORK_UNAVAILABLE', message: 'Обновление источника не удалось.' })))
    await screen.findByText('Обновление источника не удалось.')
    expect(screen.getByText('Версия источника')).toBeTruthy()
    expect(screen.queryByText(/Цитата найдена в указанном фрагменте текущего источника/)).toBeNull()
    expect(screen.getByText(/Сверка цитаты с текущей версией источника не подтверждена/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Обновить источник' }))
    await screen.findByText(/Цитата найдена в указанном фрагменте текущего источника/)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
