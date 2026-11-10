import type { WorkspaceCard, WorkspacePage } from '../workspace-contracts'
import { topicDetail } from './detail'

export const workspaceCard: WorkspaceCard = {
  kind: 'topic', id: topicDetail.topic.topic_id, version: topicDetail.topic.version, revision: 'b'.repeat(64), title: topicDetail.topic.title,
  summary: topicDetail.topic.summary, created_at: topicDetail.stats.first_seen, last_seen: topicDetail.stats.last_seen,
  status: topicDetail.topic.status, owner: topicDetail.topic.owner, ticket_count: 4, material_count: 0,
  categories: ['operational_request'], priorities: ['unknown'], unassessed: false, errors: 0, pending: false,
  synthetic: true, origins: ['authored'], source_ids: ['SYNTHETIC-001'], topic_ids: [topicDetail.topic.topic_id],
}
export const workspacePage: WorkspacePage = { items: [workspaceCard], total: 1, offset: 0, limit: 24, model_mode: 'replay' }
export const emptyArchive = { items: [], total: 0, offset: 0, limit: 20 }
