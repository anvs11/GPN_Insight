import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'

export function WorkspaceDrawer({ open, title, onClose, children, wide = false, className = '' }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean; className?: string
}) {
  const heading = useId(), panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose }, [onClose])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const oldOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panel.current?.focus()
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); return }
      if (event.key !== 'Tab') return
      const nodes = [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]') ?? [])]
        .filter(n => !n.closest('[hidden]') && n.getClientRects().length > 0)
      const first = nodes[0], last = nodes[nodes.length - 1]
      if (!first) { event.preventDefault(); panel.current?.focus() }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', key)
    return () => { document.body.style.overflow = oldOverflow; document.removeEventListener('keydown', key); if (previous?.isConnected) previous.focus() }
  }, [open])
  return <div hidden={!open} className={'drawer-backdrop ' + className} onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div role="dialog" aria-modal="true" aria-labelledby={heading} tabIndex={-1} ref={panel} className={'workspace-drawer' + (wide ? ' drawer-wide' : '')}>
      <header className="drawer-heading"><div><span className="eyebrow">GPN Insight / рабочая область</span><h2 id={heading}>{title}</h2></div>
        <button className="icon-button" onClick={onClose} aria-label={'Закрыть: ' + title}>×</button></header>
      <div className="drawer-content">{children}</div>
    </div>
  </div>
}
