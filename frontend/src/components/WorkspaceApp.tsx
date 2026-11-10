import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Health } from '../api-types'
import type { WorkspaceCard } from '../workspace-contracts'
import { cardKey } from '../workspace-labels'
import { Monitoring } from './Monitoring'
import { ReportsWorkspace } from './ReportsWorkspace'
import { WorkspaceDrawer } from './WorkspaceDrawer'
import { ImportPanel } from './ImportPanel'
import { MediaImportPanel } from './MediaImportPanel'
import { ProcessingPanel } from './ProcessingPanel'
import { ReviewPanel } from './ReviewPanel'
import '../workspace.css'

export function WorkspaceApp() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try { return localStorage.getItem('gpn-workspace-theme') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('gpn-workspace-theme', theme) } catch { /* Выбор работает и без доступа к хранилищу. */ }
  }, [theme])
  const [page, setPage] = useState<'monitoring' | 'reports'>('monitoring')
  const [tool, setTool] = useState<'text' | 'media' | 'processing' | 'review' | null>(null)
  const [selected, setSelected] = useState<WorkspaceCard[]>([]), [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0), [health, setHealth] = useState<Health | null>(null)
  const [reportBusy, setReportBusy] = useState(false)
  const selectionRef = useRef<WorkspaceCard[]>([])
  const toolPanel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    void api.health(controller.signal).then(value => { if (!controller.signal.aborted) setHealth(value) }).catch(() => { if (!controller.signal.aborted) setHealth(null) })
    return () => controller.abort()
  }, [revision, tool])
  function toggle(card: WorkspaceCard) {
    if (reportBusy) return
    setNotice('')
    const previous = selectionRef.current
    if (!previous.some(v => cardKey(v) === cardKey(card)) && previous.length >= 30) { setNotice('В одной подборке до 30 объектов. Уберите часть карточек или сохраните текущий отчёт.'); return }
    selectionRef.current = previous.some(v => cardKey(v) === cardKey(card)) ? previous.filter(v => cardKey(v) !== cardKey(card)) : [...previous, card]
    setSelected(selectionRef.current)
  }
  function closeTool() {
    // Формы остаются смонтированы: черновики и неизвестный результат записи сохраняются.
    if (toolPanel.current?.querySelector('[aria-busy="true"]')) { setNotice('Дождитесь окончания сохранения.'); return }
    setTool(null); setRevision(n => n + 1)
  }
  const titles = { text: 'Загрузка обращений', media: 'Загрузка обращений', processing: 'Обработка и состояние', review: 'Уточнение связей · необязательно' }
  return <div className="workspace-shell">
    <a className="skip-link" href="#workspace-content">К содержимому</a>
    <aside className="workspace-sidebar"><a className="brand" href="#monitoring" onClick={e => { e.preventDefault(); if (!reportBusy) setPage('monitoring') }}><span className="brand-mark" aria-hidden="true"><svg viewBox="134 54 532 522" focusable="false"><image href="/brand-source.png" width="2508" height="627" /></svg></span><span>GPN<span className="brand-light"> Insight</span><small>ПРОДУКТОВАЯ АНАЛИТИКА</small></span></a>
      <div className="product-context"><span className="product-icon">В</span><div><strong>Вебнефть</strong><small>Рабочее пространство</small></div></div>
      <nav className="main-navigation" aria-label="Основные разделы"><button className={page === 'monitoring' ? 'active' : ''} disabled={reportBusy} aria-current={page === 'monitoring' ? 'page' : undefined} onClick={() => setPage('monitoring')}><span>◎</span>Мониторинг</button>
        <button className={page === 'reports' ? 'active' : ''} disabled={reportBusy} aria-current={page === 'reports' ? 'page' : undefined} onClick={() => setPage('reports')}><span>▤</span>Отчёты <i>{selected.length}</i></button></nav>
      <div className="sidebar-section"><span className="eyebrow">РАБОТА С ДАННЫМИ</span><button disabled={reportBusy} onClick={() => setTool('text')}><span>＋</span>Загрузить обращения</button><button disabled={reportBusy} onClick={() => setTool('processing')}><span>▷</span>Обработка</button><details className="optional-tools"><summary>Дополнительные действия</summary><button disabled={reportBusy} onClick={() => setTool('review')}><span>◇</span>Уточнить связи</button></details></div>
      <div className="sidebar-bottom"><span className={'connection-dot ' + (health ? 'connected' : '')} /><div><strong>{health ? 'Сервер доступен' : 'Связь не подтверждена'}</strong><small>{health?.model_mode === 'replay' ? 'Replay · известные записи' : health?.model_mode === 'live' ? 'Live' : 'Проверьте запуск сервера'}</small></div></div>
    </aside>
    <div className="workspace-main"><header className="workspace-topbar"><div>Рабочее пространство <span>/</span> <strong>{page === 'monitoring' ? 'Мониторинг' : 'Отчёты'}</strong></div><div className="topbar-actions"><span className="environment-pill">Локальный пилот</span><button className="button-secondary theme-toggle" aria-pressed={theme === 'dark'} onClick={() => setTheme(value => value === 'light' ? 'dark' : 'light')}><span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>{theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}</button><button className="button-secondary" disabled={reportBusy} onClick={() => setTool('text')}>＋ Загрузить данные</button></div></header>
      <main id="workspace-content" className="workspace-content">
        {notice && <p role="alert" className="workspace-notice">{notice}</p>}
        <div hidden={page !== 'monitoring'}><Monitoring selected={selected} onToggle={toggle} onReports={() => setPage('reports')} refresh={revision} /></div>
        <div hidden={page !== 'reports'}><ReportsWorkspace selected={selected} onRemove={toggle} onRefresh={cards => { selectionRef.current = cards; setSelected(cards) }} onMonitoring={() => setPage('monitoring')} onBusy={setReportBusy} /></div>
        <footer className="workspace-footer">GPN Insight <span>Потребности с проверяемыми основаниями. Решения остаются человеку.</span></footer>
      </main>
    </div>
    <WorkspaceDrawer open={tool !== null} title={tool ? titles[tool] : 'Инструменты'} onClose={closeTool} wide>
      <div ref={toolPanel} className="tools-content" onClick={event => {
        const link = event.target instanceof Element ? event.target.closest('a[href^="#"]') : null
        const href = link?.getAttribute('href')
        if (href === '#processing-title' || href === '#review-title' || href === '#topic-list-title') {
          event.preventDefault()
          if (href === '#topic-list-title') { closeTool(); setPage('monitoring') }
          else setTool(href === '#processing-title' ? 'processing' : 'review')
        }
      }}>{(tool === 'text' || tool === 'media') && <div className="tool-tabs"><button className={tool === 'text' ? 'active' : ''} onClick={() => setTool('text')}>Текст</button><button className={tool === 'media' ? 'active' : ''} onClick={() => setTool('media')}>Медиа</button></div>}
        {tool === 'processing' && <p className="tool-introduction">Обработка читает уже импортированные обращения, извлекает потребности и объединяет их в темы. В replay доступны только известные входы; новые файлы сами по себе не создают сохранённые ответы. Прогресс и ошибки появятся здесь, результаты — в мониторинге.</p>}
        <div hidden={tool !== 'text'}><ImportPanel onImported={() => setRevision(n => n + 1)} /></div>
        <div hidden={tool !== 'media'}><MediaImportPanel onImported={() => setRevision(n => n + 1)} /></div>
        <div hidden={tool !== 'processing'}><ProcessingPanel requireImport importRevision={revision} /></div>
        <div hidden={tool !== 'review'}><ReviewPanel /></div>
      </div>
    </WorkspaceDrawer>
  </div>
}
