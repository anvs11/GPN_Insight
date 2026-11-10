import type { ReportSnapshot } from '../workspace-contracts'
import { formatTopicDate } from '../topic-labels'

// Только текст и известные заголовки серверного Markdown. HTML/ссылки/скрипты не интерпретируются.
function plain(value: string) {
  return value.replace(/\\([\\`*_{}[\]()#+.!|>~-])/g, '$1').replaceAll('&lt;', '<').replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"').replaceAll('&#x27;', "'").replaceAll('&amp;', '&')
}
export function ReportDocument({ snapshot }: { snapshot: ReportSnapshot }) {
  return <section id="print-report" className="surface saved-report-text">
    <header className="document-heading"><span className="eyebrow">GPN INSIGHT / СОХРАНЁННЫЙ ОТЧЁТ</span>
      <p>{formatTopicDate(snapshot.generated_at)} МСК · Режим сервера: {snapshot.model_mode}</p></header>
    <div className="report-document">{snapshot.markdown.split('\n').map((line, index) => {
      if (line.startsWith('### ')) return <h4 key={index}>{plain(line.slice(4))}</h4>
      if (line.startsWith('## ')) return <h3 key={index}>{plain(line.slice(3))}</h3>
      if (line.startsWith('# ')) return <h2 key={index}>{plain(line.slice(2))}</h2>
      if (!line.trim()) return null
      return <p key={index} className={line.startsWith('- ') ? 'document-bullet' : ''}>{plain(line)}</p>
    })}</div>
    <footer className="document-footnote">Снимок {snapshot.report_id}. {snapshot.limitations}</footer>
  </section>
}
