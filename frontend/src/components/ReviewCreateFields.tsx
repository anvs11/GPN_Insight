import type { ProposedTopic, ReviewItem, SourceDetail } from '../api-types'
import { reviewEvidence } from '../review-create'
import { PassportExamples } from './PassportExamples'

const fields = [
  ['title', 'Название новой темы'], ['summary', 'Краткое описание новой темы'],
  ['scenario', 'Сценарий пользователя новой темы'], ['need_statement', 'Потребность новой темы'],
  ['scope', 'Границы включения и исключения новой темы'],
] as const

export function ReviewCreateFields({ proposal, review, sources, onChange }: {
  proposal: ProposedTopic; review: ReviewItem; sources: SourceDetail[]; onChange: (value: ProposedTopic) => void
}) {
  const options = reviewEvidence(review, sources)
  return <section aria-label="Паспорт создаваемой темы">
    <p>Тема создаётся человеком (authored), а не моделью. Границы паспорта останутся неподтверждёнными (scope_confirmed=false).
      Автор и причина решения попадут в историю. Все потребности этого Review будут связаны с новой темой; прежние назначения будут сняты.</p>
    {fields.map(([key, label]) => <label key={key}>{label}<textarea rows={key === 'scope' ? 4 : 2}
      required value={proposal[key]} onChange={event => onChange({ ...proposal, [key]: event.target.value })} /></label>)}
    <p>Цитаты доступны только из текущих потребностей Review. Текст цитаты менять нельзя.
      {sources.some(source => source.synthetic) && ' Источники синтетические — не статистика компании.'}</p>
    {options.some(option => !option.verified) && <p className="topic-notice">Часть цитат не подтверждена текущими источниками. Создание заблокировано до проверки источников.</p>}
    <PassportExamples kind="inclusion" examples={proposal.inclusion_examples} options={options}
      onChange={value => onChange({ ...proposal, inclusion_examples: value })} />
    <PassportExamples kind="exclusion" examples={proposal.exclusion_examples} options={options}
      onChange={value => onChange({ ...proposal, exclusion_examples: value })} />
  </section>
}
