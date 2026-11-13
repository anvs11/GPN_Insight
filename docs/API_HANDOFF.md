# API тем и решений — передача участнику 4

Новый backend срез 09.11 A4–A7: [partial/revisions/assessment/weekly/synthetic binary API, примеры и точные команды](PARTIAL_WEEKLY_HANDOFF.md). Новые read поля совместимы со старым frontend, есть sourceMediaUrl/weeklyReportUrl. Кнопки/плеер/покрытие в UI ещё должен подключить У4; browser-click прогон заблокирован Computer Use, HTTP/restart проверены. Старые API работают, нижние записи исторические.

Обновление A1–A3 09.11: SourceDetail содержит additive original_access=available|restricted; прежние ответы без поля совместимы с UI. Все публичные source/card/list/decision/passport/merge-preview скрывают nonsynthetic ticket.text/requester_id/имя файла/URL, не меняя raw SQLite. Нормализованные фрагменты сохраняются с прежними ограничениями masking; это не enterprise authorization. Новые partial/media DTO отдельно, production media ещё не включён: [PARTIAL_MEDIA_FOUNDATION.md](PARTIAL_MEDIA_FOUNDATION.md).

Запрос U4 об импорте (07.11.2026): текстовые preview/commit/history уже в main 60eedf3; локальное media-расширение описано в [U1_MEDIA_HANDOFF.md](U1_MEDIA_HANDOFF.md) и [IMPORT_HTTP.md](IMPORT_HTTP.md). Публикация media, binary-source API и браузерная приёмка ещё не выполнены. Контракт источников/provenance для U2 — [MEDIA_ANALYSIS_HANDOFF.md](MEDIA_ANALYSIS_HANDOFF.md).

Актуализация 2026-11-07: GET списка/карточки поддерживает согласованные фильтры/период, `period_stats` — новое nullable поле, `stats` остаётся all-time. Добавлен PATCH /topics/{id}/passport с version/passport_version/author/reason, history через changes и 409. Полные тела/примеры/команды — [SERVER_COMPLETION.md](SERVER_COMPLETION.md); 587 backend tests и реальный HTTP/CLI/restart прошли. 14 workflow-примеров и 38 schemas экспортируются прежними CLI-командами. UI новых действий не менялся; прежние записи об unsupported-фильтрах ниже относятся к этапу 1.

Текущий серверный срез 2026-11-07: POST /review-items/{id}/resolve (+alias /reviews), GET history, полный PATCH статусов, POST merge-preview/merge/exclude, GET changes и Markdown GET /reports (+alias /report). Request/response, строгие версии, повторы и реальные команды — [HUMAN_WORKFLOW.md](HUMAN_WORKFLOW.md), 11 synthetic JSON-примеров — data/contracts/workflow-examples.json. 515 backend-тестов, HTTP/CLI/restart и сохранность данных проверены. UI этих действий ещё не подключён и остаётся задачей U4; нижние описания минимального этапа исторические.

Предыдущий срез 2026-11-06 добавил GET /api/v1/reviews (status=open по умолчанию, также resolved|insufficient_data|all) и GET /api/v1/reviews/{review_id}. Эти пути сохранены, добавлены aliases /review-items. Ответы — сохранённые ReviewItem с version/requires_recheck; 404 REVIEW_NOT_FOUND для неизвестного ID, 422 для неверного/повторного фильтра. Стык U2 и ограничения — [TEAM_INTEGRATION.md](TEXT_INTEGRATION.md). Frontend в этой среде отдельно не запускался.

Готовность анализа (2026-11-06): обычный backend может подключиться по явному серверному профилю, но реальные настройки этой сессии пока отсутствуют. REPLAY_READY у модели не равен pipeline_ready: учитывать integration_errors, включая PROCESSING_PROFILE_*/REPLAY_PROFILE_CONFLICT. Без готовой сборки POST /runs сохраняет 503 INTEGRATION_NOT_READY, /imports независим. Mode/origins не скрывать: проверочная конфигурация явно replay/authored, не живая Qwen. Новых HTTP DTO нет; [RUNTIME_PROFILE.md](RUNTIME_PROFILE.md).

Новая передача участнику 4 (2026-11-06): готовы POST /api/v1/imports?dry_run=true, POST /api/v1/imports и GET /api/v1/imports. Формат multipart, существующие ImportPreview/ImportResult/ImportLogEntry, русские 422/413/409, FormData-пример и ограничения — [IMPORT_HTTP.md](IMPORT_HTTP.md), синтетические ответы — data/contracts/import-http-examples.json. Форма остаётся вашей зоной: правила JSON/идентичности/counts выполняет backend. Неготовность анализа не блокирует импорт и не скрывается.

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

В списке тем нет периодов и остальных фильтров: неизвестные параметры возвращают `422 UNSUPPORTED_FILTER`. Полный набор фильтров ExecPlan остаётся следующей задачей. POST/GET runs и worker теперь реализованы как инфраструктура — см. [PIPELINE.md](PIPELINE.md); без компонентов 1/2 POST возвращает 503 INTEGRATION_NOT_READY. Импорт, review, merge, файлы медиа и полный жизненный цикл через HTTP ещё не подключены. `/` возвращает 404 до появления frontend.

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

Ожидается: `176 passed` и известное предупреждение Starlette/AnyIO; 16 общих и 7 HTTP-примеров; `No broken requirements found`; SQLite v3 готова; Uvicorn слушает 127.0.0.1:8000. Без дополнительных настроек текстовый replay сообщает REPLAY_NOT_CONFIGURED, ASR/VL — REPLAY_NOT_IMPLEMENTED, pipeline_ready=false: интеграции участников 1/2 ещё нет.

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
