# Завершить самостоятельные серверные операции рассмотрения

Этот ExecPlan дополняет .agent/EXECPLAN.md и поддерживается по .agent/PLANS.md. Рабочая папка C:/Users/user/projects/GPN_Insight, существующая feat/server-text-integration. На старте сохранялись19 файлов предыдущего среза; main/origin/main=cad1574. После реализации пользователь отдельно поручил ревью и commit/push этой feature-ветки, merge в main выполняет сам. Новых клонов и изменений рабочих баз задача не требует.

## Purpose / Big Picture

Аналитик сможет разрешить сохранённое спорное назначение, сохранить остальные статусы решения, объединить темы, исключить неверную связь и получить отчёт из SQLite. Все действия сохраняются после рестарта, повтор не создаёт второе действие, устаревшие версии отклоняются. Это ручные серверные операции по уже описанным продуктовым правилам, без изменения автоматических алгоритмов U2, импорта U1 и UI U4.

## Progress

- [x] (2026-11-07, перед публикацией) Повторное ревью не выявило существенных дефектов. Снова515 passed, примеры/pip check и настоящий HTTP/CLI/restart прошли; отчёт var/human-workflow-check-f5ae5e54337d46c1896353df7092658f/result.json. Сверены32 файла, .env/четыре SQLite и чужие компоненты сохранены, scan/diff без находок. Пользователь поручил commit/push feature-ветки, merge оставил за собой.
- [x] (2026-11-07) Проверены папка/ветка/status и существующие repositories/DTO/ExecPlan; прежние изменения сохранены. Выявлен прежний путь /review-items в плане и локальный /reviews: сохранить оба через aliases, не ломать текущих потребителей.
- [x] (2026-11-07) Часть A: request/response ручного review и атомарные attach/create/insufficient_data; проверены версии/история/повтор/rollback/конкуренция/recheck и новый профиль Pipeline после ручного выбора.
- [x] (2026-11-07) Часть B: полный PATCH решения/issue URL, сохранность дат/сигналов по created_at и строгой границе внедрения; прямые model calls отсутствуют.
- [x] (2026-11-07) Часть C: preview/commit merge и exclude, overrides/history/receipts/canonical chains, distinct source_id; исходное решение при пустом target тоже требует подтверждения.
- [x] (2026-11-07) Часть D: Markdown HTTP/CLI из одного snapshot, экранирование, unknown requester, отдельные period/all-time и run coverage/errors, запрет overwrite output. Валидны11 synthetic JSON-примеров; проверен реальный HTTP/CLI/restart.
- [x] (2026-11-07) 515 tests/один прежний warning, примеры16/7+proposal+11workflow, 35schemata, pip check и прежние import/runtime/pipeline прошли. Последний отчёт var/human-workflow-check-db23181717d44fb5b153a477c4db2f9c/result.json. Подготовлена передача; пользователь подтвердил отсутствие продолжения U2, на следующей интеграции остановиться. Рабочие данные/прежние правки сохраняются, без commit/push.

## Surprises & Discoveries

Объединение решённой темы в нерешённую цель требует явного подтверждения; иначе исходный активный выбор скрывался бы без предупреждения. Дополнительная регрессия это проверяет. Неверный ?dry_run=true у mutation теперь422 до записи. Нельзя использовать обычный write_text для --output отчёта: открытие x защищает БД от случайной перезаписи, тест проверяет hash файла после отказа.

Хранение ReviewResolution/ManualOverride/DecisionEvent и более широких бизнес-статусов уже существует. Необходим сервис, объединяющий эти операции одной SQLite-транзакцией. ExecPlan требует точный повтор подтверждённого review возвращать как сохранённый результат; одного сравнения version недостаточно. Для этого добавляется необязательный request_hash к сохранённому ReviewResolution, без новой SQL-таблицы. Старые JSON читаются с null.

## Decision Log

Решение (2026-11-07, участник 3): действовать по уже утверждённым правилам ручных операций, добавить новые server-owned DTO и описать HTTP до реализации в этом плане. Это не изменение политики автоматического принятия U2 и не требует подменять чужой модуль review.py. Часть U3 размещается в app/workflow.py и отдельном workflow API. Вновь создаваемый паспорт берётся из ProposedTopic человека, ID/продукт/связи задаёт сервер. Ручной new не требует полного модельного поиска.

Решение (2026-11-07): POST /review-items/{id}/resolve и alias /reviews/{id}/resolve принимают version, action, author, reason, topic_versions, topic_id для attach либо proposed_topic для create. Сервер проверяет все затронутые версии и текущие версии needs/источников, сохраняет overrides/связи/закрытие review вместе. Response — ReviewItem; темы после успеха читаются GET. Точное повторение JSON-команды возвращает сохранённое решение без нового override. Duplicate-review остаётся дополнением вне этого этапа.

Решение (2026-11-07): merge получает version источника и target_version, target_topic_id, author/reason и явное confirm_decision_conflict. Read-only merge-preview возвращает обе карточки и конфликт; при commit обе версии перепроверяются. Exclude получает version, уникальные need_ids, author/reason. Повторы merge/exclude хранят request_hash и небольшой результат в существующем TopicChangeEvent; новые optional JSON-поля не требуют миграции SQLite и не меняют модельные response schemas/replay hashes.

## Outcomes & Retrospective

Четыре серверные части завершены, 37 новых поведенческих случаев и515 всего прошли. Реальный ordinary HTTP/CLI/restart подтвердил действия/повторы/409/сохранность и report. Старые импортные/runtime/checkpoint сценарии повторены. Контракты передаются через docs/HUMAN_WORKFLOW.md, новые изменения остаются локальными. Live/new/media/UI/полная сценарная запись и независимая оценка не завершены; пользователь подтвердил, что следующей части U2 ещё нет, поэтому дальнейший общий модельный сценарий ждёт интеграции.

## Context and Orientation

backend/app/storage.py хранит источники/review/overrides, repositories.py — темы/решения/счётчики. workflow.py объединяет ручные действия; workflow_api.py публикует их. schemas.py содержит новые DTO; report.py строит текст из snapshot БД. api.py сохраняет прежние ответы и PATCH. pipeline.py уже учитывает manual overrides и ревизию каталога. Все проверки — на отдельных synthetic/authored SQLite в var, без вызовов моделей.

## Plan of Work

Часть A добавляет ResolveReviewRequest и service-метод, использует save_review/create_topic/record_override с транзакцией и серверным временем. На recheck/устаревшие needs — 409; неверные ссылки/поля — 422/404. Проверить также несколько потребностей, выбор темы вне candidate_ids, конкуренцию, отказ посередине, повтор после потери ответа и новый source_version после ручного решения.

Часть B снимает старое 501 для linked/deferred/declined/implemented и сохраняет прежний DecisionUpdate. Для HTTP URL задачи проверять allowlist APP_ISSUE_HOSTS, https и отсутствие credentials; простой ID задачи допустим без URL. Бизнес-решение не создаёт AnalysisFeedback. Даты/показатели проверять по created_at/Europe-Moscow, не дате позднего импорта.

Часть C переносит связи и их историю при merge, сохраняет целевое решение, исходная тема получает merged/canonical ID, каталог меняет ревизию. Ручные override не теряются. На старом ID GET сообщает конечную основную тему по цепочке; циклы/merged target/self/разные продукты отвергаются. Exclude снимает только выбранные текущие связи, сохраняет override и open review атомарно; повтор не создаёт новую очередь.

Часть D строит report без моделей из одного DB snapshot. Различать уникальные источники/потребности/темы/заявителей, режимы, authored, pending и run errors. Без данных заявителей — «нет данных». У Markdown экранировать недоверенные поля/ссылки и не включать исходные обращения/секреты. CLI и GET /report используют одну функцию. Не обещать качество, live, эффект или интеграцию UI.

## Concrete Steps

Из backend использовать ..\.venv\Scripts\python.exe -X utf8 -m pytest -q с профильными файлами после каждой части, затем весь набор. app.cli validate-examples/export-schemas и pip check. Отдельная scripts.check_human_workflow поднимет собственный Uvicorn на свободном localhost-порту, загрузит только отдельную synthetic фикстуру, выполнит реальные действия/повторы/409 и рестарт и запишет result.json в var. Все временные процессы остановить.

## Validation and Acceptance

Review до разрешения не считает спорный need; attach/create/insufficient_data атомарны и не отменяются повторным pipeline. Одновременные разные решения одной review version дают один успех/один 409. Повтор точной принятой команды не создаёт историю заново. После source update прежний review становится требует recheck.

Статусы сохраняются с required fields/version; старая карточка409, история прежних решений неизменна. Merge сохраняет target decision/историю и уникальные источники, обе версии обязательны, конфликт требует явного подтверждения. Exclude+review+override откатываются вместе, прежний auto-match не возвращает неверную связь. Отчёт не использует модельные числа и явно подписывает mode/origin/ошибки. После рестарта всё читается из SQLite.

## Idempotence and Recovery

SQL schema v3 и старые таблицы не заменяются. Optional поля для provenance/receipt читаются из JSON старых записей с default None; новые операции подтверждаются после commit. Снимок прежних незакоммиченных файлов и хеши личных данных сохранить в var/backups до правок. Для повторных тестов новая synthetic БД; рабочую SQLite не сбрасывать. При интеграционном ограничении не включать fake adapter в production.

## Artifacts and Notes

Записать фактические числа тестов и пути отчётов после выполнения. Новые JSON-примеры пометить synthetic/contract-only; серверные ID/time в командах заполнять из реально прочитанных ответов. Памятка нового чата не должна продолжать утверждать, что действия review отсутствуют после завершения.

## Interfaces and Dependencies

HumanWorkflow(repository).resolve_review(review_id, ResolveReviewRequest) -> ReviewItem. merge_topics/exclude_needs возвращают небольшие typed results; весь commit внутри repo.transaction. Не давать модели Repository/файлы/команды. Зависимости и Python3.13 остаются прежними. Новая сеть кроме собственных localhost-проверок не нужна. Request/response новые endpoints описать в docs/HUMAN_WORKFLOW.md для U4; их UI не меняется в этой задаче.

## Revision Note

2026-11-07: ограниченный план четырёх самостоятельных серверных частей, с сохранением прежних правок и явной границей интеграции других участников.

2026-11-07: все части реализованы/проверены, записаны515 тестов/реальные HTTP/CLI/restart, aliases reports/report и review-items/reviews. Передача U2/U4 готова, продолжения U2 нет; дальнейшее model-new/record не подменяется ручным create.
