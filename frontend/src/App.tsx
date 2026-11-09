import { useState } from 'react'
import { ProcessingPanel } from './components/ProcessingPanel'
import { ImportPanel } from './components/ImportPanel'
import { ReviewPanel } from './components/ReviewPanel'
import { TopicList } from './components/TopicList'

export default function App() {
  const [importRevision, setImportRevision] = useState(0)
  return (
    <main>
      <h1>GPN Insight</h1>
      <p>Анализ продуктовой обратной связи</p>

      <ImportPanel onImported={() => setImportRevision(value => value + 1)} />

      <ProcessingPanel requireImport importRevision={importRevision} />

      <TopicList />

      <ReviewPanel />
    </main>
  )
}
