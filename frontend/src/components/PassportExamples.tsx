import type { EvidenceRef, PassportExample } from '../api-types'
import { sameValue } from '../passport-verification'

export type EvidenceOption = { ref: EvidenceRef; verified: boolean }

export function PassportExamples({ kind, examples, options, onChange }: {
  kind: 'inclusion' | 'exclusion'; examples: PassportExample[]; options: EvidenceOption[];
  onChange: (value: PassportExample[]) => void
}) {
  const label = kind === 'inclusion' ? 'включения' : 'исключения'
  function update(index: number, value: PassportExample) { onChange(examples.map((item, i) => i === index ? value : item)) }
  return <section aria-label={'Примеры ' + label + ' паспорта'}>
    <h5>Примеры {label}</h5>
    {examples.length === 0 && <p>Примеры не заданы.</p>}
    {examples.map((example, index) => <fieldset key={index}>
      <legend>Пример {label} {index + 1}</legend>
      <label>Текст примера {label} {index + 1}<textarea rows={3} required value={example.text}
        onChange={event => update(index, { ...example, text: event.target.value })} /></label>
      <p>Выберите подтверждающие цитаты. Совпадение текста не заменяет проверку смысла человеком.</p>
      {options.length === 0 && <p>Нет доступных цитат. Проверьте источники карточки.</p>}
      {options.map(({ ref, verified }) => {
        const checked = example.evidence_refs.some(item => sameValue(item, ref))
        return <label className="passport-evidence-choice" key={JSON.stringify(ref)}>
          <input type="checkbox" checked={checked} disabled={!verified && !checked} onChange={event => update(index, {
            ...example, evidence_refs: event.target.checked ? [...example.evidence_refs, ref] :
              example.evidence_refs.filter(item => !sameValue(item, ref)),
          })} />
          <span>{ref.source_id} · {ref.fragment_id}: «{ref.quote}»{!verified && ' — текущий фрагмент не подтверждён'}</span>
        </label>
      })}
      <button type="button" onClick={() => onChange(examples.filter((_, i) => i !== index))}>Удалить пример {label} {index + 1}</button>
    </fieldset>)}
    <button type="button" onClick={() => onChange([...examples, { text: '', evidence_refs: [] }])}>Добавить пример {label}</button>
  </section>
}
