import type { TopicStatus } from './api-types'

export const topicStatusLabels: Record<TopicStatus, string> = {
  new: 'Новая', needs_research: 'Исследуется', linked: 'Передана в разработку', deferred: 'Отложена',
  declined: 'Отклонена', implemented: 'Реализована', merged: 'Объединена',
}

export function formatTopicDate(value: string | null) {
  return value === null ? 'Нет данных' : new Date(value).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })
}
