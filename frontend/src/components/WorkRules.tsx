import { useId, useState, type ReactNode } from 'react'

/** Подсказка доступна с клавиатуры и по касанию; ошибки сюда не помещаются. */
export function WorkRules({ children }: { children: ReactNode }) {
  const id = useId(), [opened, setOpened] = useState(false)
  return <div className={'work-rules' + (opened ? ' is-open' : '')}>
    <button type="button" className="rules-trigger" aria-expanded={opened} aria-controls={id}
      onClick={() => setOpened(value => !value)} onKeyDown={event => { if (event.key === 'Escape') setOpened(false) }}><span aria-hidden="true">?</span> Правила работы</button>
    <div id={id} className="rules-popover">{children}</div>
  </div>
}
