import { ProcessingPanel } from './components/ProcessingPanel'
import { TopicList } from './components/TopicList'

export default function App() {
  return (
    <main>
      <h1>GPN Insight</h1>
      <p>Анализ продуктовой обратной связи</p>

      <ProcessingPanel />

      <TopicList />
    </main>
  )
}
