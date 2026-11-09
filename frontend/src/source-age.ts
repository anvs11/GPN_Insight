export function formatSourceAge(createdAt: string, now: number): string {
  const created = Date.parse(createdAt)
  if (!Number.isFinite(created) || !Number.isFinite(now)) return 'Не удалось определить возраст'
  if (created > now) return 'Дата обращения ещё не наступила — проверьте исходную дату'
  const minutes = Math.floor((now - created) / 60_000)
  if (minutes === 0) return 'Меньше минуты'
  const days = Math.floor(minutes / 1440)
  const hours = Math.floor(minutes / 60) % 24
  return `${days > 0 ? `${days} д. ` : ''}${days > 0 || hours > 0 ? `${hours} ч. ` : ''}${minutes % 60} мин.`
}
