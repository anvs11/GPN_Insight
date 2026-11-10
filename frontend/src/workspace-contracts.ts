import type { Category, Priority } from './processing-contracts'
import { categoryLabels, priorityLabels } from './processing-contracts'

export interface WorkspaceCard {
  kind: 'topic' | 'source'; id: string; version: number; revision: string; title: string; summary: string
  created_at: string | null; last_seen: string | null; status: string; owner: string | null
  ticket_count: number; material_count: number; categories: Category[]; priorities: Priority[]
  unassessed: boolean; errors: number; pending: boolean; synthetic: boolean; origins: string[]; source_ids: string[]; topic_ids: string[]
}
export interface WorkspacePage { items: WorkspaceCard[]; total: number; offset: number; limit: number; model_mode: 'live' | 'replay' }
export interface Selection { kind: 'topic' | 'source'; id: string; version: number; revision: string }
export interface ReportDraft { title: string; summary: string; comment: string; selections: Selection[] }
export interface ArchiveEntry { report_id: string; title: string; kind: 'selection' | 'weekly'; generated_at: string; model_mode: 'live' | 'replay'; period: unknown; selection_count: number }
export interface ReportSnapshot extends ArchiveEntry { format_version: 'report-snapshot-v1'; digest: string; markdown: string; summary: string; comment: string; limitations: string; cards: WorkspaceCard[]; selections: Selection[] }
export interface ArchivePage { items: ArchiveEntry[]; total: number; offset: number; limit: number }

const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string')
const date = (v: unknown) => typeof v === 'string' && Number.isFinite(Date.parse(v))
const mode = (v: unknown) => v === 'live' || v === 'replay'
export function isWorkspaceCard(v: unknown): v is WorkspaceCard {
  return object(v) && (v.kind === 'topic' || v.kind === 'source') && typeof v.id === 'string' && !!v.id &&
    count(v.version) && v.version >= 1 && typeof v.revision === 'string' && /^[0-9a-f]{64}$/.test(v.revision) && typeof v.title === 'string' && typeof v.summary === 'string' &&
    (v.created_at === null || date(v.created_at)) && (v.last_seen === null || date(v.last_seen)) && typeof v.status === 'string' &&
    (v.owner === null || typeof v.owner === 'string') && count(v.ticket_count) && count(v.material_count) && count(v.errors) &&
    strings(v.categories) && v.categories.every(c => Object.hasOwn(categoryLabels, c)) &&
    strings(v.priorities) && v.priorities.every(p => Object.hasOwn(priorityLabels, p)) && typeof v.unassessed === 'boolean' &&
    typeof v.pending === 'boolean' && typeof v.synthetic === 'boolean' && strings(v.origins) && strings(v.source_ids) && strings(v.topic_ids)
}
export function isWorkspacePage(v: unknown): v is WorkspacePage {
  return object(v) && Array.isArray(v.items) && v.items.every(isWorkspaceCard) && count(v.total) && count(v.offset) && count(v.limit) &&
    v.limit > 0 && v.items.length <= v.limit && v.items.length <= v.total && mode(v.model_mode) &&
    new Set(v.items.map(i => i.kind + ':' + i.id)).size === v.items.length
}
export function isArchiveEntry(v: unknown): v is ArchiveEntry {
  return object(v) && typeof v.report_id === 'string' && !!v.report_id && typeof v.title === 'string' &&
    (v.kind === 'selection' || v.kind === 'weekly') && date(v.generated_at) && mode(v.model_mode) && count(v.selection_count) && 'period' in v
}
export function isArchivePage(v: unknown): v is ArchivePage {
  return object(v) && Array.isArray(v.items) && v.items.every(isArchiveEntry) && count(v.total) && count(v.offset) && count(v.limit) &&
    v.limit > 0 && v.items.length <= v.limit && v.items.length <= v.total
}
export function isSnapshot(v: unknown): v is ReportSnapshot {
  return isArchiveEntry(v) && object(v) && v.format_version === 'report-snapshot-v1' && typeof v.digest === 'string' && /^[0-9a-f]{64}$/.test(v.digest) &&
    typeof v.markdown === 'string' && v.markdown.length < 2 * 1048576 && typeof v.summary === 'string' && typeof v.comment === 'string' &&
    typeof v.limitations === 'string' && Array.isArray(v.cards) && v.cards.every(isWorkspaceCard) &&
    Array.isArray(v.selections) && v.selections.every(s => object(s) && (s.kind === 'source' || s.kind === 'topic') &&
      typeof s.id === 'string' && count(s.version) && s.version > 0 && typeof s.revision === 'string' && /^[0-9a-f]{64}$/.test(s.revision)) && v.selections.length === v.selection_count
}
