# Фильтры тем, ручной паспорт и сценарный replay

Серверный срез U3 от 2026-11-07 выполняется в существующей `feat/server-text-integration` поверх cold-start и прежних локальных исправлений. HTTP/CLI используют существующие репозитории и SQLite v3; миграция/сброс не нужны. Обычный production API не вставляет примеры. UI не менялся.

## Быстрая безопасная проверка

Из корня репозитория в PowerShell:

```powershell
Set-Location (Join-Path (git rev-parse --show-toplevel) 'backend')
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_server_completion
```

Последняя команда сама готовит отдельную явно synthetic SQLite в новом `var/server-completion-check-…`, запускает собственный ordinary Uvicorn на свободном localhost-порту, проверяет HTTP/рестарт, затем выполняет CLI record/verify в двух процессах. Рабочая `.env`, базы и уже открытый пользовательский сервер не меняются. Собственные процессы закрываются в `finally`.

Ожидаемые ключевые строки:

```text
filters: combined=200; all-time=4; separate MSK period; empty=[]; duplicate-filter=422
passport: PATCH=200; topic=3/passport=2; stale=409; decision/history/count/ID preserved
restart: confirmed passport, title, owner and history preserved
scenario: separate CLI record/verify; steps=9; records=11; exact trace; origins=authored; unknown rejected
live record: unavailable; exit=1; active set preserved; working files unchanged
PASS: actual HTTP/CLI/restart and offline scenario; no working database or model calls
```

`validate-examples` проверяет 16 общих/7 HTTP-примеров, ProposedTopic и 14 workflow-примеров; `pip check` сообщает `No broken requirements found.` На macOS/Linux использовать `../.venv/bin/python` вместо пути Windows. Результаты и текущий полный счётчик tests фиксируются в ExecPlan.

## GET списка и карточки для участника 4

`GET /api/v1/topics` возвращает прежний массив `TopicDetail`. Без query остаются повторяющиеся немерженные темы. Все фильтры однократные, комбинируются:

| Параметр | Значения и смысл |
|---|---|
| product_id | только `webneft`, это default |
| support_level | `repeated` (default), `single`, `all`; повторяемость по всем активным источникам темы |
| status | `new`, `needs_research`, `linked`, `deferred`, `declined`, `implemented`, `merged`; без параметра merged исключены |
| review_due | только `true`/`false`, пересмотр deferred по текущей дате Москвы |
| date_from/date_to | `YYYY-MM-DD`, включительно по `created_at` текущих активных источников в Europe/Moscow; можно задать одну границу |

Например:

```http
GET /api/v1/topics?product_id=webneft&status=needs_research&support_level=all&date_from=2026-10-01&date_to=2026-10-31
GET /api/v1/topics?status=deferred&review_due=true
GET /api/v1/topics?status=merged&support_level=all
GET /api/v1/topics/{topic_id}?date_from=2026-10-01&date_to=2026-10-31
```

Период отбирает темы, у которых есть хотя бы один активный источник за период. `stats` всегда остаётся all-time, `sources/evidence` также не обрезаются. Новое аддитивное поле `period_stats` содержит те же программные показатели и `date_from/date_to`; без периода оно `null`. Например, `stats.ticket_count=4`, а `period_stats.ticket_count=2`. В карточке пустой период даёт `period_stats.ticket_count=0`; в списке такая тема исключается. `support_level` фильтруется по all-time, поэтому repeated-тема может иметь одно свидетельство за период. Не подменять эти два числа в UI.

Несколько потребностей одного source_id считаются одним обращением. Неизвестные requester_id дают `null`, не ноль. Pending-источники не считаются активными доказательствами до завершения нового анализа. Пустой ответ списка `[]` не объявляет отсутствие потребностей.

Неизвестные/повторные параметры, неверные значения/даты и перевёрнутый период — 422; они не игнорируются. Карточка принимает только две даты. Конкретные synthetic-примеры запросов и периодного результата: `data/contracts/topic-filter-examples.json`; они проверены через настоящий HTTP-слой в `test_handoff_json_examples_match_actual_http`.

## PATCH паспорта для участника 4

`PATCH /api/v1/topics/{topic_id}/passport` принимает:

```json
{
  "version": 2,
  "passport_version": 1,
  "author": "Синтетический аналитик",
  "reason": "Проверены цитаты и границы",
  "title": "Уточнённое название",
  "passport": {
    "scenario": "Сценарий из текущего паспорта",
    "need_statement": "Проверенная потребность",
    "scope": "Проверенные границы",
    "inclusion_examples": [{
      "text": "Подтверждённый пример",
      "evidence_refs": [{
        "source_id": "SYNTHETIC-ID",
        "fragment_id": "SYNTHETIC-FRAGMENT",
        "quote": "Дословная цитата существующего фрагмента"
      }]
    }],
    "exclusion_examples": [],
    "scope_confirmed": true
  }
}
```

Этот фрагмент объясняет поля; ID/цитату нельзя выдумывать. Готовое тело для явно загруженной demo-фикстуры находится в `data/contracts/workflow-examples.json` под `schema=PassportUpdate`. Приложение берёт реальные версии и существующие evidence из GET карточки/источника. `version` и `passport_version` — **текущие** версии, strict JSON integers; следующие назначает сервер. `scope_confirmed` — обязательный strict JSON boolean; inclusion_examples непустой. `author/reason` обязательны и не могут состоять из пробелов. `title` необязателен.

Тело не принимает product_id/ID/версию внутри passport, бизнес-решение, summary или связи. 200 возвращает именно сохранённый `TopicDetail`: topic.version и passport.version увеличены на один, catalog_revision изменён, ID/связи/счётчики/решение/его история сохранены. Изменённый человеком текст помечается `data_origin=authored`; `analysis_mode` сохраняет режим прежнего анализа, `synthetic` по источникам не меняется.

`GET /api/v1/topics/{id}/changes` содержит `kind=passport`, before/after, автора, причину и время. История решения в `history` остаётся отдельной. Ошибка истории откатывает паспорт и каталог вместе. Stale topic/passport version или merged-тема — 409; неверная цитата/продукт/тело — 422. Query, включая `dry_run`, не принимается. Для merged использовать canonical_topic_id.

После 409 перечитать карточку и предложить человеку проверить актуальные границы. После потерянного ответа сначала GET/changes, затем явная новая правка с актуальными версиями. Старый PATCH не повторяется как безусловная запись и не создаёт вторую историю. Повторный pipeline не отменяет вручную сохранённый паспорт; изменение каталога во время назначения даёт CATALOG_CHANGED.

## Полный сценарный record/verify

Из `backend/` в PowerShell:

```powershell
$scenarioRoot = "..\var\scenario-demo-" + [guid]::NewGuid().ToString('N')
..\.venv\Scripts\python.exe -X utf8 -m app.cli record `
    --scenario ..\data\scenarios\text_flow.json --authored-scenario `
    --recordings $scenarioRoot
..\.venv\Scripts\python.exe -X utf8 -m app.cli verify-recordings `
    --scenario ..\data\scenarios\text_flow.json --recordings $scenarioRoot
```

Обе команды возвращают JSON: `verified=true`, `steps=9`, `record_count=11`, `origins=["authored"]`, `model_calls=false`, `network_used=false`; `trace_hash` совпадает. Record дополнительно сообщает `published=true`. Каждый capture/verify использует **свою новую** SQLite в `var/scenario-runs/…`; рабочая APP_DB_PATH не участвует. Верификатор не вызывает authored fixture или live gateway.

Проверяемые действия:

| Шаг | Проверка |
|---|---|
| base | Пустая БД, три synthetic обращения/четыре потребности, cold new, match и open review; две темы имеют общий source_id |
| decision | Решение needs_research, stale отказ 409 |
| passport | Обычный repository update, версия/название/подтверждение границ до delta |
| repeat | Повтор импорта unchanged=3 и 10 переиспользованных шагов без новых результатов |
| delta | Новый source_id пополняет прежнюю тему; модельный запрос содержит обновлённый паспорт/решение |
| review | Ручное attach с версиями и точным повтором без дублей |
| exclude | Запрет неверной связи, open review; повторный анализ не возвращает связь |
| merge | Общий source_id не удваивает счётчик, целевое решение сохраняется |
| restart | Новое соединение/worker, 13 reused, тема/паспорт/решение/история/canonical ID сохранены |

Дополнительно verifier импортирует намеренно неизвестное изменение текста и требует `completed_with_errors/REPLAY_RECORD_MISSING`, pending и сохранность решения. Это ожидаемый отрицательный probe: успешный JSON означает, что отказ подтверждён. Если неизвестная запись нужна в положительном потоке, CLI завершается ненулевым кодом с `REPLAY_RECORD_MISSING`. Отдельная HTTP-процедура выше проверяет настоящий перезапуск процесса Uvicorn; шаг сценария restart проверяет reopen/worker. Реальное interrupted/reuse покрывает существующая `scripts.check_pipeline`.

`data/scenarios/initial_state.json` задаёт поддерживаемый v1 initial snapshot — полностью пустую SQLite v3. `text_flow.json` фиксирует synthetic-источники, clock, порядок действий и authored-описания. Это отдельный набор проверки процесса, не benchmark и не holdout. Рабочие snapshot не экспортируются, прочие initial-state форматы отвергаются.

Сохраняются точные request/schema/prompt/profile hashes, нормализация и recording_set_version. Fragment/need/topic ID и версии сравниваются точно. Семантический trace исключает только случайные audit/run UUID и transport-mode, сохраняет время/решения/паспорта/связи/статистику. Clock передаётся только внутренним CLI/test-контекстом, в том числе worker; HTTP/.env не могут его задавать. Изменение clock/spec/initial snapshot/profile/trace/записей не принимается как прежний сценарий.

Набор хранится в immutable `sets/…`: JSON спецификации/начального состояния/профиля/trace, строгие text recordings и manifest с хешами всех файлов. После capture выполняется отдельный полный strict verify; только затем, с блокировкой публикации и повторной проверкой записанных bytes, атомарно меняется `active.json`. Предыдущий указатель сохраняется `active.backup.…json`. Прежняя recording_set_version с другим смысловым содержимым запрещена даже после смены активного набора. Ошибка/обрыв до переключения оставляют прежний набор активным; unreferenced staging не используется. Stale lock автоматически не снимается — проверить процесс либо взять новый каталог.

Результат record находится только в `var/`; команды не меняют `data/demo`, эти сценарные входы или holdout. Публикация произвольного пути вне `var` запрещена. Старые команды record отдельных authored JSON и verify-recordings без `--scenario` сохранены.

Без явного `--authored-scenario` сценарный record сейчас даёт `LIVE_RECORDING_NOT_CONNECTED`, exit=1 и не меняет active. Настоящий model record требует проверенного внутреннего TextGateway; он не заменён fixture. ASR/VL, медиа/файловое восстановление U1, UI U4, LARGE_100/оценка качества/независимая разметка и полный мультимодальный показ этим срезом не закрыты.

## Передача команде

Участнику 4: фильтры, nullable `period_stats`, PassportUpdate/38 экспортированных schemas, 14 workflow-примеров и указанные 200/409/422. Подключение экранов остаётся за UI.

Участнику 2: алгоритмы/промпты не переписывались; ручная правка меняет catalog_revision, delta в строгом сценарии видит реальный новый паспорт. Authored trace не является оценкой модели. Следом нужны реальный протокол/gateway и размеченный набор для качества.

Участнику 1: импорт/normalization/source hash/контракт материалов сохранены. Сценарий использует существующий текстовый import; медиа подключаются отдельно через согласованные интерфейсы, без обещания общей файловой/SQLite-транзакции.
