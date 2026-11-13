import { useId, useState } from 'react'
import type { WorkspaceCard } from '../workspace-contracts'

function orbit(index: number, total: number, radius: number) {
  const angle = -Math.PI / 2 + index * 2 * Math.PI / total
  return [145 + Math.cos(angle) * radius, 150 + Math.sin(angle) * radius]
}

export function SignalMap({ cards, onOpen }: { cards: WorkspaceCard[]; onOpen: (card: WorkspaceCard) => void }) {
  const topics = cards.filter(c => c.kind === 'topic')
  const sources = [...new Set(topics.flatMap(t => t.source_ids))]
  const gradient = useId().replaceAll(':', ''), [active, setActive] = useState<string | null>(null)
  const selected = topics.find(t => t.id === active)
  const positions = topics.map((_, i) => orbit(i, topics.length, topics.length === 1 ? 0 : 77))
  const points = sources.map((_, i) => orbit(i, sources.length, 128))
  const radius = Math.min(16, 200 / Math.max(1, topics.length))
  return <section className="signal-map"><div className="map-heading"><span className="eyebrow">СВЯЗИ ПО ИСТОЧНИКАМ</span><span className="map-live">2D</span></div>
    <h3>Карта сигналов</h3><p>Тем: {topics.length} · связанных обращений: {sources.length}</p>
    {topics.length ? <svg viewBox="0 0 290 305" role="group" aria-label="Фактические связи тем с обращениями на текущей странице">
      <defs><radialGradient id={gradient}><stop offset="0%" stopColor="#9c94ff" /><stop offset="100%" stopColor="#51499a" /></radialGradient></defs>
      <circle className="map-orbit" cx="145" cy="150" r="128" /><circle className="map-orbit inner-orbit" cx="145" cy="150" r="77" />
      {topics.flatMap((t, i) => sources.map((s, j) => t.source_ids.includes(s) && <path className={'signal-edge' + (selected?.id === t.id ? ' highlighted' : selected ? ' dimmed' : '')} key={JSON.stringify([t.id, s])} d={`M ${positions[i][0]} ${positions[i][1]} L ${points[j][0]} ${points[j][1]}`} />))}
      {sources.map((s, j) => <g key={s} className={selected && !selected.source_ids.includes(s) ? 'map-source-muted' : ''}><circle cx={points[j][0]} cy={points[j][1]} r="7" className="source-halo" /><circle cx={points[j][0]} cy={points[j][1]} r={sources.length > 80 ? 1.8 : 3} className="source-node" /><title>Обращение {s}</title></g>)}
      {topics.map((t, i) => <g key={t.id} tabIndex={0} role="button" aria-label={'Тема на карте: ' + t.title} onMouseEnter={() => setActive(t.id)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(t.id)} onBlur={() => setActive(null)} onClick={() => onOpen(t)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(t) } }}>
        <circle cx={positions[i][0]} cy={positions[i][1]} r={radius + 6} className="topic-halo" />
        <circle cx={positions[i][0]} cy={positions[i][1]} r={radius} fill={t.priorities.includes('potentially_critical') ? '#b95f6e' : `url(#${gradient})`} className="map-topic" />
        <text x={positions[i][0]} y={positions[i][1] + 4} textAnchor="middle" pointerEvents="none" style={{ fontSize: topics.length > 16 ? 9 : 11 }}>{i + 1}</text><title>{t.title} · обращений: {t.source_ids.length}</title></g>)}
    </svg> : <div className="map-empty">Связи появятся после получения тем</div>}
    <div className="map-legend"><span>◉ Тема</span><span>● Обращение</span></div>
    {topics.length > 0 && <><p className="map-selection-caption">{selected ? `${selected.title} · связанных обращений: ${selected.source_ids.length}` : 'Наведите на тему, чтобы выделить её связи. Нажмите, чтобы открыть карточку.'}</p>
      <ol className="map-topic-list">{topics.map((t, i) => <li key={t.id}><button className={selected?.id === t.id ? 'active' : ''} onMouseEnter={() => setActive(t.id)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(t.id)} onBlur={() => setActive(null)} onClick={() => onOpen(t)}><span>{i + 1}</span>{t.title}<small>{t.source_ids.length}</small></button></li>)}</ol></>}
    <small>Все темы текущей страницы с учётом фильтров. Общее обращение показано одной точкой. Неразобранное без темы в карту не входит. Положение узлов не означает сходство или приоритет.</small>
  </section>
}
