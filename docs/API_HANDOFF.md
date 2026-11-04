# API тем и решений — передача участнику 4

Реализован минимальный HTTP-сценарий: список тем → карточка → источник → решение → повторное чтение после рестарта. Все ответы читаются из SQLite. Обычный запуск не создаёт примеры; пустая база возвращает `[]`. Тестовая процедура `app.cli seed-demo` отдельно загружает явно синтетическую фикстуру `data/contracts/demo_state.json`. Она не вызывает модели и не заменяет импорт участника 1.

## Контракт

Базовый путь — `/api/v1`. Vite должен проксировать `/api` на `http://127.0.0.1:8000`: браузер обращается к относительным путям. CORS не включён; frontend в этом этапе не реализуется.

| Запрос | Реализованный ответ |
|---|---|
| `GET /health` | Сервер/SQLite отдельно от доступности моделей |
| `GET /topics` | `TopicDetail[]`, из базы; по умолчанию только repeated и не merged |
| `GET /topics?support_level=single` | Темы с менее чем двумя активными источниками |
| `GET /topics/{topic_id}` | `TopicDetail`: topic, stats, sources, history и происхождение |
| `GET /sources/{source_id}` | `SourceDetail` текущей версии источника продукта webneft |
| `PATCH /topics/{topic_id}/decision` | Сохранённый `TopicDetail`; доступны `new` и `needs_research` |
| `GET /openapi.json` (от корня сервера) | Полная схема реализованных запросов/ответов |

`DecisionUpdate` требует `status`, непустые `owner`/`reason` и целое JSON-число `version >= 1` из последней прочитанной `topic.version`. Строка `"1"`, `true`, `1.0`, пропуски и строки только из пробелов отвергаются с `422 VALIDATION_ERROR`. Другие корректно заполненные бизнес-статусы пока дают `501 FEATURE_NOT_IMPLEMENTED`; `merged` через эту ручку не выставляется. Неизвестные тема/источник — `404 TOPIC_NOT_FOUND` / `SOURCE_NOT_FOUND`.

`new` — явный возврат темы на первичное рассмотрение. Он сохраняет автора и причину в новом событии, увеличивает версию и не удаляет прежние решения. Даже повтор такого же решения с актуальной версией создаёт событие истории. Повтор со старой версией — `409 VERSION_CONFLICT`, без изменения темы и истории. При 409 перечитайте карточку и предложите человеку проверить чужое изменение; не подменяйте версию автоматически и не повторяйте запись скрыто. Сохранение подтверждается только после HTTP 200. Версия ответа PATCH соответствует именно этой записи.

`stats.ticket_count` рассчитан по уникальным активным source_id. `requester_count=null` означает отсутствие данных, его нельзя заменять нулём. `SourceDetail.document` может быть `null` до нормализации, `needs=[]` вместе с `requires_analysis=true` не означает «потребностей нет». Учитывайте `document.errors` и `TopicDetail.pending_source_ids`. Показывайте `synthetic`, `data_origin`, `model_mode`, `analysis_mode`; `replay` при `analysis_mode=null` и `authored` не является результатом модели. Текст и цитаты выводите как текст, без HTML из данных.

В этом этапе нет периодов и остальных фильтров списка: неизвестные параметры возвращают `422 UNSUPPORTED_FILTER`. Полный набор фильтров ExecPlan остаётся следующей задачей. Импорт, run, review, merge, файлы медиа и полный жизненный цикл через HTTP здесь не реализованы. `/` возвращает 404 до появления frontend.

## JSON-примеры

Все файлы в UTF-8. [http-examples.json](../data/contracts/http-examples.json) помечен `synthetic=true`, `purpose=http_contract_examples_only`: пять ответов — пустой список, карточка, источник, 409 и 422. Тема и источник получены из отдельной синтетической базы и сверяются тестом с настоящими HTTP-ответами. Это образцы контракта, не результаты модели и не данные компании.

[decision.json](../data/contracts/decision.json) — запрос `needs_research`; [decision-new.json](../data/contracts/decision-new.json) — запрос `new`. Их исходная версия 1 служит примером; перед каждым новым сохранением подставляется последняя прочитанная версия.

## Проверка: терминал 1, PowerShell

Из корня проекта. Нужна существующая `.venv` с зависимостями README. Сначала остановите старый сервер на 8000 через `Ctrl+C`. Новая уникальная база сохраняет ваши прежние данные; запомните напечатанный путь, если собираетесь закрывать терминал.

```powershell
Set-Location C:\Users\user\projects\GPN_Insight
$env:APP_DB_PATH = 'var/api-handoff-' + [guid]::NewGuid().ToString('N') + '.db'
$env:APP_MODEL_MODE = 'replay'
Write-Output "Тестовая база: $env:APP_DB_PATH"
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\python.exe -X utf8 -m app.cli init-db
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Ожидается: `62 passed` и известное предупреждение Starlette/AnyIO; 16 общих и 7 HTTP-примеров; `No broken requirements found`; SQLite v2 готова; Uvicorn слушает 127.0.0.1:8000. В health модели честно возвращают `REPLAY_NOT_IMPLEMENTED` — это ожидаемое ограничение текущей сборки.

## Терминал 2: пустая база

```powershell
Set-Location C:\Users\user\projects\GPN_Insight
$baseUrl = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$baseUrl/health" | ConvertTo-Json -Depth 10
$cards = Invoke-RestMethod "$baseUrl/topics"
$cards.Count
```

Ожидается `0`. Если сервер недоступен, проверьте, что терминал 1 остаётся открытым и Uvicorn запущен.

## Терминал 1: отдельно загрузить синтетическую фикстуру

Остановите Uvicorn через `Ctrl+C`. В том же терминале, из `backend/`, с прежним APP_DB_PATH:

```powershell
..\.venv\Scripts\python.exe -X utf8 -m app.cli seed-demo
..\.venv\Scripts\python.exe -X utf8 -m app.cli seed-demo
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Первый seed сообщает о тестовой карточке и отсутствии анализа моделей. Второй — `unchanged`, история не сбрасывается. Обычный production-запуск не включает seed-demo.

## Терминал 2: чтение и сохранение

```powershell
$cards = Invoke-RestMethod "$baseUrl/topics"
$cards.Count
$topicId = $cards[0].topic.topic_id
$card = Invoke-RestMethod "$baseUrl/topics/$topicId"
$card.topic | Select-Object topic_id, status, owner, version | Format-List
$card.stats | Select-Object ticket_count, requester_count | Format-List
$card | Select-Object synthetic, data_origin, model_mode, analysis_mode | Format-List
$source = Invoke-RestMethod "$baseUrl/sources/DEMO-T001"
$source | Select-Object source_version, requires_analysis, synthetic, data_origin | Format-List

$decision = Get-Content -Raw -Encoding UTF8 data/contracts/decision.json | ConvertFrom-Json
$decision.version = $card.topic.version
$body = [Text.Encoding]::UTF8.GetBytes(($decision | ConvertTo-Json -Depth 10))
$saved = Invoke-RestMethod "$baseUrl/topics/$topicId/decision" `
    -Method Patch -ContentType 'application/json; charset=utf-8' -Body $body
$saved.topic | Select-Object status, owner, version | Format-List
$saved.history | Select-Object owner, reason, topic_version | Format-List
```

На новой тестовой базе: одна тема, `ticket_count=4`, `requester_count` пустой (`null`), `synthetic=True`, `data_origin=authored`, `analysis_mode=null`. До сохранения — `new`, версия 1; после — `needs_research`, версия 2, `Тестовый владелец` и русская причина в истории. Отдельный `Format-List` предотвращает смешение колонок разных объектов форматтером PowerShell.

Проверка защиты — отправить **тот же body**, сохранив старую версию:

```powershell
try {
    Invoke-RestMethod "$baseUrl/topics/$topicId/decision" `
        -Method Patch -ContentType 'application/json; charset=utf-8' -Body $body | Out-Null
    throw 'Ошибка проверки: ожидался HTTP 409.'
} catch {
    if ($null -eq $_.Exception.Response) { throw }
    $code = [int]$_.Exception.Response.StatusCode
    if ($code -ne 409) { throw }
    Write-Output "HTTP $code VERSION_CONFLICT — устаревшее решение отклонено."
}
```

Затем проверить возврат в `new` с заново прочитанной версией:

```powershell
$card = Invoke-RestMethod "$baseUrl/topics/$topicId"
$decision = Get-Content -Raw -Encoding UTF8 data/contracts/decision-new.json | ConvertFrom-Json
$decision.version = $card.topic.version
$body = [Text.Encoding]::UTF8.GetBytes(($decision | ConvertTo-Json -Depth 10))
$saved = Invoke-RestMethod "$baseUrl/topics/$topicId/decision" `
    -Method Patch -ContentType 'application/json; charset=utf-8' -Body $body
$saved.topic | Select-Object status, owner, version | Format-List
$saved.history | Select-Object new_status, owner, reason, topic_version | Format-List
$saved.stats.ticket_count
```

Ожидается `new`, версия 3, прежние идентификатор темы и счётчик 4, два события истории. Владелец сохранён; возврат в new не очищает историю.

## Проверка после рестарта

Терминал 1: `Ctrl+C`, затем в том же терминале и той же `backend/` (не генерируйте новый APP_DB_PATH):

```powershell
..\.venv\Scripts\python.exe -X utf8 -m app.cli init-db
..\.venv\Scripts\python.exe -X utf8 -m app.cli seed-demo
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Терминал 2 с прежними `$topicId` и `$saved`:

```powershell
$restored = Invoke-RestMethod "$baseUrl/topics/$topicId"
if (($restored.topic | ConvertTo-Json -Depth 30 -Compress) -ne
    ($saved.topic | ConvertTo-Json -Depth 30 -Compress)) { throw 'Тема изменилась после рестарта.' }
if (($restored.history | ConvertTo-Json -Depth 30 -Compress) -ne
    ($saved.history | ConvertTo-Json -Depth 30 -Compress)) { throw 'История изменилась после рестарта.' }
if ($restored.stats.ticket_count -ne 4) { throw 'Неверный счётчик.' }
$restored.topic | Select-Object status, owner, version | Format-List
$restored.history | Select-Object new_status, owner, reason, topic_version | Format-List
Write-Output 'PASS: тема, кириллица, история и счётчик сохранены после рестарта.'
```

Ожидается явный `PASS` и те же версия 3, два события, счётчик 4. Если терминал 1 был закрыт, откройте новый, установите **напечатанный ранее** APP_DB_PATH и APP_MODEL_MODE=replay, перейдите в backend и запустите Uvicorn. Смена базы покажет другое состояние; это не потеря решений. После проверки остановите Uvicorn через `Ctrl+C`. `.env` и прежние базы эта процедура не меняет.
