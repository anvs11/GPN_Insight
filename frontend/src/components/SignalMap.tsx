import { useId, useState } from 'react'
import type { WorkspaceCard } from '../workspace-contracts'

export function SignalMap({ cards, onOpen }: { cards: WorkspaceCard[]; onOpen: (card: WorkspaceCard) => void }) {
  const topics = cards.filter(c => c.kind === 'topic').slice(0, 4)
  const sources = [...new Set(topics.flatMap(t => t.source_ids))].slice(0, 6)
  const gradient = useId().replaceAll(':', ''), [active, setActive] = useState<string | null>(null)
  const positions = [[75, 84], [208, 110], [109, 212], [220, 249]]
  const points = [[27, 166], [146, 42], [260, 181], [49, 277], [163, 305], [264, 317]]
  return <section className="signal-map"><div className="map-heading"><span className="eyebrow">СВЯЗИ ПО ИСТОЧНИКАМ</span><span className="map-live">2D</span></div>
    <h3>Карта сигналов</h3><p>Темы и подтверждающие обращения</p>
    {topics.length ? <svg viewBox="0 0 290 355" role="img" aria-label="Фактические связи тем с обращениями на текущей странице">
      <defs><radialGradient id={gradient}><stop offset="0%" stopColor="#9c94ff" /><stop offset="100%" stopColor="#51499a" /></radialGradient></defs>
      <ellipse className="map-orbit" cx="145" cy="175" rx="118" ry="143" /><ellipse className="map-orbit inner-orbit" cx="145" cy="175" rx="70" ry="95" />
      {topics.flatMap((t, i) => sources.map((s, j) => t.source_ids.includes(s) && <path className={'signal-edge' + (active === t.id ? ' highlighted' : active ? ' dimmed' : '')} key={t.id + s} d={`M ${positions[i][0]} ${positions[i][1]} Q 145 175 ${points[j][0]} ${points[j][1]}`} />))}
      {sources.map((s, j) => <g key={s}><circle cx={points[j][0]} cy={points[j][1]} r="11" className="source-halo" /><circle cx={points[j][0]} cy={points[j][1]} r="4.5" className="source-node" /><title>Обращение {s}</title></g>)}
      {topics.map((t, i) => <g key={t.id} tabIndex={0} role="button" aria-label={'Тема на карте: ' + t.title} onClick={() => onOpen(t)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(t) } }}>
        <circle cx={positions[i][0]} cy={positions[i][1]} r="28" className="topic-halo" onMouseEnter={() => setActive(t.id)} onMouseLeave={() => setActive(null)} />
        <circle cx={positions[i][0]} cy={positions[i][1]} r="20" fill={t.priorities.includes('potentially_critical') ? '#b95f6e' : `url(#${gradient})`} className="map-topic" onMouseEnter={() => setActive(t.id)} onMouseLeave={() => setActive(null)} />
        <text x={positions[i][0]} y={positions[i][1] + 5} textAnchor="middle" pointerEvents="none">{i + 1}</text><title>{t.title} · обращений: {t.ticket_count}</title></g>)}
    </svg> : <div className="map-empty">Связи появятся после получения тем</div>}
    <div className="map-legend"><span>◉ Тема</span><span>● Обращение</span></div>
    <small>До четырёх тем текущей страницы. Положение узлов не означает сходство или критичность.</small>
  </section>
}
