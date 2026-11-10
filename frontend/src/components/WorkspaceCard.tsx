import type { WorkspaceCard as Card } from '../workspace-contracts'
import { categoryLabels, priorityLabels } from '../processing-contracts'
import { formatTopicDate } from '../topic-labels'
import { cardStatus } from '../workspace-labels'
import { SourceAgeText } from './SourceAge'

export function WorkspaceCard({ card, selected, onSelect, onOpen }: { card: Card; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  const critical = card.priorities.includes('potentially_critical')
  return <article className={'insight-card' + (critical ? ' card-critical' : '')} onClick={event => {
    if (event.target instanceof Element && event.target.closest('button,input,label,a')) return
    onOpen()
  }}>
    <div className="card-topline"><span className="card-type">{card.kind === 'topic' ? 'ТЕМА' : 'ОБРАЩЕНИЕ'} <span>· {card.id.slice(0, 12)}</span></span>
      <label className="card-check"><input type="checkbox" checked={selected} onChange={onSelect} aria-label={'В отчёт: ' + card.title} /><span className="sr-only">Выбрать</span></label></div>
    <div className="card-badges">{card.categories.map(c => <span key={c} className="category-badge">{categoryLabels[c]}</span>)}
      {!card.categories.length && <span className="category-badge neutral">Категория не определена</span>}</div>
    <h3><button className="card-title" onClick={onOpen}>{card.title}</button></h3>
    <p className="card-summary">{card.summary || 'Откройте источник, чтобы проверить содержание и состояние обработки.'}</p>
    <div className="card-priorities">{card.priorities.map(p => <span key={p} className={'priority-badge priority-' + p}><i />{priorityLabels[p]}</span>)}
      {card.unassessed && <span className="priority-badge priority-unknown">Есть потребности без оценки</span>}</div>
    {(card.errors > 0 || card.pending) && <p className="card-warning">{card.errors > 0 ? `Ошибок материалов: ${card.errors}. ` : ''}{card.pending ? 'Нужна повторная проверка.' : 'Покрытие неполное.'}</p>}
    <div className="card-facts"><div><span>{card.kind === 'topic' ? 'Обращений' : 'Вложений'}</span><strong>{card.kind === 'topic' ? card.ticket_count : card.material_count}</strong></div>
      <div><span>{card.kind === 'topic' ? 'Первое обращение (МСК)' : 'Создано (МСК)'}</span><strong>{formatTopicDate(card.created_at)}</strong></div></div>
    {card.kind === 'topic' && <p className="card-owner"><span>{card.owner?.slice(0, 1).toUpperCase() ?? '—'}</span>{card.owner ?? 'Ответственный не назначен'}</p>}
    <footer><span className={'status-pill status-' + card.status}>{cardStatus(card)}</span><button className={'quick-report ' + (selected ? 'is-selected' : '')} onClick={onSelect} aria-pressed={selected} aria-label={(selected ? 'Убрать из отчёта: ' : 'Добавить в отчёт: ') + card.title}>{selected ? '✓ В отчёте' : '+ В отчёт'}</button></footer>
    <small className="card-origin">{card.synthetic ? 'Синтетические данные · ' : ''}{card.origins.includes('authored') ? 'Подготовлено вручную' : card.origins.includes('model') ? 'Ответ модели' : 'Импортировано'}
      {card.kind === 'source' && card.created_at && <> · <SourceAgeText createdAt={card.created_at} /></>}</small>
  </article>
}
