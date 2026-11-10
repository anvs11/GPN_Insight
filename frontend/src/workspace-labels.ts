import type { WorkspaceCard } from './workspace-contracts'
import type { TopicStatus } from './api-types'
import { topicStatusLabels } from './topic-labels'

export const sourceStateLabels: Record<string, string> = { pending: 'Ожидает анализа', complete: 'Обработано', partial: 'Частично обработано', failed: 'Ошибка обработки', empty: 'Без пригодного текста' }
export function cardStatus(card: WorkspaceCard) { return card.kind === 'topic' ? topicStatusLabels[card.status as TopicStatus] ?? card.status : sourceStateLabels[card.status] ?? card.status }
export function cardKey(card: Pick<WorkspaceCard, 'kind' | 'id'>) { return card.kind + ':' + card.id }
