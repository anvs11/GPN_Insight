import { ProcessingPanel } from './components/ProcessingPanel'

export default function App() {
  return (
    <main>
      <h1>GPN Insight</h1>
      <p>Анализ продуктовой обратной связи</p>

      <ProcessingPanel />

      <article>
        <p><strong>Демонстрационные данные</strong></p>
        <p>Статичный макет карточки, не результат обработки сервером.</p>
        <h2>Экспорт списка заявок</h2>
        <p>Пользователям нужен способ выгрузить отфильтрованный список заявок.</p>
      </article>
    </main>
  )
}
