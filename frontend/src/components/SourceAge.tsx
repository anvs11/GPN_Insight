import { useEffect, useState } from 'react'
import { formatSourceAge } from '../source-age'

export function SourceAge({ createdAt }: { createdAt: string }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  return <div><dt>Возраст обращения</dt><dd>{formatSourceAge(createdAt, now)}</dd>
    <small>От даты создания, по часам устройства. Статус закрытия в исходной системе неизвестен; это не срок SLA.</small></div>
}
