import { useState } from 'react'
import { ProcessingPanel } from './components/ProcessingPanel'
import { ImportPanel } from './components/ImportPanel'
import { ReviewPanel } from './components/ReviewPanel'
import { TopicList } from './components/TopicList'
import { MediaImportPanel } from './components/MediaImportPanel'
import { WeeklyReport } from './components/WeeklyReport'

export default function App() {
  const [importRevision, setImportRevision] = useState(0)
  return (
    <main>
      <h1>GPN Insight</h1>
      <p>Анализ продуктовой обратной связи</p>
      <nav aria-label="Разделы сервиса" className="topic-actions">
        <a href="#import-title">Текстовый импорт</a><a href="#media-import-title">Медиаимпорт</a>
        <a href="#processing-title">Обработка</a><a href="#topic-list-title">Темы</a>
        <a href="#review-title">Проверка</a><a href="#weekly-report-title">Недельный отчёт</a>
      </nav>

      <ImportPanel onImported={() => setImportRevision(value => value + 1)} />
      <MediaImportPanel onImported={() => setImportRevision(value => value + 1)} />

      <ProcessingPanel requireImport importRevision={importRevision} />

      <TopicList />

      <ReviewPanel />
      <WeeklyReport />
    </main>
  )
}
