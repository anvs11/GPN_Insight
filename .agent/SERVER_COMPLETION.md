# Завершить независимые серверные операции и сценарный replay

Этот ExecPlan поддерживается по .agent/PLANS.md и дополняет .agent/EXECPLAN.md. Работа выполняется в существующей feat/server-text-integration. Сохранены 21 прежняя локальная правка и приватные хеши в var/backups/server-polish-8f665efe49254256bd1e30ab58dd6360. Пользователь поручил три части: фильтры тем, HTTP правки паспорта и полный текстовый сценарный replay до границы подключения остальных участников.

## Purpose / Big Picture

Участник 4 сможет читать темы с фильтрами и сохранять проверенные границы паспорта без потери решения. Команда получит воспроизводимый backend-сценарий импорт→cold-start→решение→правка паспорта→повтор→delta→review→exclude→merge→reopen с отдельной БД, точными запросами и видимыми отказами. Синтетическая authored запись доказывает процесс, а не качество/доступность модели.

## Progress

- [x] (2026-11-07, предкоммитное ревью) Повторно проверены 45 task-файлов, контракты/модельные схемы и сохранность пяти приватных файлов; credential patterns=0, существенных дефектов не найдено. Снова 587 passed/один прежний warning, примеры/pip check и реальный ordinary HTTP/CLI/restart успешны: var/server-completion-check-672807aea6b4452dadcfa230090b760c/result.json. Remote main/ветка сверены fetch; пользователь поручил commit/push этого среза, merge оставил за собой.
- [x] (2026-11-07) Проверены текущая ветка/правки, ExecPlan/CONCEPT/правила и существующие repository/API/replay. Снимок прежних изменений и хеши .env/рабочих SQLite сохранены. База исходного среза: 541 passed.
- [x] (2026-11-07) Часть A: фильтры продукта/status/support/period/review_due, строгие 422, отдельный nullable period_stats, MSK/active distinct counts; stats и default repeated/nonmerged сохранены. Изменение ответа аддитивное, старый HTTP-пример дополнен period_stats=null.
- [x] (2026-11-07) Часть B: PATCH passport с author/reason, двумя версиями, evidence/merged/409, авторской provenance, атомарными history/catalog и неизменными решениями; ошибки/конкуренция/reopen проверены.
- [x] (2026-11-07) Часть C: restricted v1 text flow и initial empty state, clock/profile/trace binding, strict replay, 9 действий/11 authored записей, backup/atomic pointer/history-version guard и unknown/ненулевые отказы. Live явно не подключён.
- [x] (2026-11-07) 587 tests passed (46 новых), один известный Starlette/AnyIO warning; 16/7+proposal+14workflow, 38 schemas и pip check успешны. Настоящие Uvicorn/HTTP/CLI/restart: var/server-completion-check-6bdd874251c3429f9616fd1cfc78dfe0/result.json. Передача — docs/SERVER_COMPLETION.md.

## Surprises & Discoveries

Repository.update_passport уже существует, увеличивает topic/passport version, пишет TopicChangeEvent и catalog_revision; HTTP нет. Для него нужны проверки автора/причины и merged. GET /topics пока поддерживает только support_level, остальные фильтры отвергаются. Старый record/verify проверяет отдельные записи, но не воспроизводит действия/состояние. Для сценария требуется управляемое время в worker без изменения рабочих .env и без глобального monkeypatch процесса сервера.

По результатам: аддитивный period_stats потребовал обновить прежний HTTP golden JSON; старые поля не менялись. Изменённый человеком model-паспорт теперь authored при сохранении прежнего analysis_mode. CLI сценария сохраняет REPLAY_RECORD_MISSING положительного потока вместо общего assertion-кода. Случайный audit UUID нельзя использовать как oracle воспроизводимости, поэтому trace исключает только audit/run IDs и transport-mode, не source/fragment/need/topic IDs или версии/время.

## Decision Log

Решение (2026-11-07, U3): фильтры GET /topics: product_id=webneft, date_from/date_to (inclusive Europe/Moscow по created_at текущих активных источников), status, support_level=single|repeated|all (default repeated), review_due=true|false. Неизвестные/повторные/некорректные параметры — 422. По умолчанию merged исключены, явный status=merged разрешён. Support_level относится к all-time данным; период отбирает темы с хотя бы одним активным источником и возвращает отдельный period_stats с границами, stats остаётся all-time. GET карточки поддерживает тот же период.

Решение (2026-11-07): PATCH /topics/{id}/passport принимает version текущей темы, passport_version текущего паспорта, author/reason, optional title и passport с содержимым scenario/need_statement/scope/inclusion_examples/exclusion_examples/scope_confirmed. Product/ID/следующие версии назначает сервер. Scope confirmation — явный strict bool, inclusion_examples непустые. Response TopicDetail, история GET changes; stale/merged — 409. Чужие/неподтверждённые цитаты — 422. Не меняются summary, бизнес-решение, связи и model response schemas.

Решение (2026-11-07): сценарий — ограниченный декларативный synthetic текстовый набор, не произвольный код или SQL. Initial snapshot только пустая SQLite v3, проверяется и хешируется. Clock задаётся CLI-контекстом и переносится в worker через copy_context, в HTTP нет управления временем. Содержательные source/fragment/need/topic ID и версии сравниваются точно; случайные audit/run ID не участвуют в семантическом trace, фиксированные даты участвуют. При нарушении записи/сценария активный указатель не меняется. Режимы authored/live не подменяются; CLI live record пока явно недоступен.

Решение (2026-11-07): использовать отдельный сценарный registry над прежними text recordings, не изменяя model schemas/request hash/старую публикацию. Он связывает JSON spec/snapshot/profile/trace и все записи checksum manifest, повторно проверяет bytes до atomic pointer. Только var-output, новый каталог БД для каждого capture/verify; отсутствующий live gateway — LIVE_RECORDING_NOT_CONNECTED. Это завершённый процессный текстовый срез, не полный multimedia/LARGE_100/quality benchmark.

## Outcomes & Retrospective

Три части завершены, 587 backend tests и настоящий HTTP/CLI/restart прошли. Рабочие .env/четыре SQLite сохранены по SHA-256; прежние 21 локальная правка сохранены, ключевой предыдущий код сравнен побайтно. Алгоритмы/промпты, импорт/normalizer, frontend, SQL migrations и зависимости не менялись в этом срезе. Дальше нужна интеграция UI U4, media/файловых версий U1, реального gateway и независимого набора качества U1/U2. По текущему поручению пользователя проверенный срез передаётся commit/push через feat/server-text-integration; merge пользователь выполняет самостоятельно.

## Context and Orientation

backend/app/api.py публикует чтение/решения; workflow_api.py — ручные операции. repositories.py хранит темы/историю/статистику, storage.py — источники/транзакции. schemas.py — общие DTO; model response schemas при этой работе не меняются. app/replay.py реализует строгие TextRecording/request hashes и публикацию записей. app/cli.py получает сценарный флаг к существующим record/verify. Новый модуль scenario.py воспроизводит действия через обычные importer/RunService/HumanWorkflow/Repository на свежей изолированной SQLite; synthetic gateway находится только в scripts и подключается при явном authored-флаге.

## Plan of Work

Часть A переиспользует расчёт статистики, добавляет отдельный периодный DTO и согласованный read snapshot списка. Не заменять all-time числа периодными и не удваивать source_id при нескольких needs. Проверить границы MSK, неизвестных заявителей, фильтры и пустой ответ.

Часть B добавляет HTTP DTO и строит TopicPassport с server product/version. Сохранение и response получаются в одной транзакции. Укрепить существующий update_passport, проверить конфликт решения/паспорта, откат ошибки истории, конкуренцию, merged и reopen, повторный pipeline не отменяет ручную правку.

Часть C фиксирует сценарный контракт до реализации. Проверка работает исключительно ReplayGateway и не получает live factory. Record с явным authored-input использует только fixture; будущий live factory остаётся интерфейсом и не заявляется готовым. Указатель сценарного набора с manifest/checksums публикуется атомарно после полного capture и отдельного strict verify; прежний указатель сохраняется. Verify поднимает свежую БД, проверяет clock/spec/initial state/profile/trace и неизвестный запрос; рабочие базы и demo/holdout не изменяются.

## Concrete Steps

Из backend запускать ..\.venv\Scripts\python.exe -X utf8 -m pytest -q с новыми профильными файлами, затем весь набор. app.cli validate-examples/export-schemas, pip check. Новые точные CLI record/verify-команды и безопасные HTTP примеры добавить в docs/SERVER_COMPLETION.md и README после реализации. Для ручной проверки поднимать собственный Uvicorn на свободном localhost-порту с отдельной SQLite/профилем; останавливать только собственные процессы.

## Validation and Acceptance

Без query GET /topics сохраняет прежние repeated/nonmerged. Фильтры комбинируются без потери данных, дубликаты и неверные значения не дают unfiltered success. Период показывает отдельный distinct source/requester count и MSK границы. PATCH passport сохраняет тему/решение/историю, увеличивает обе версии/каталог, даёт 409 устаревшему клиенту и откатывает все изменения при ошибке истории.

Сценарий начинается с действительно пустой SQLite, получает new/match/review и меняет паспорт обычной операцией до delta; точные запросы отражают изменение версии/границ. Повтор не создаёт дублей. Review/exclude/merge и восстановление сохраняют решения, счётчик merge использует distinct source_id при общем обращении. Неизвестная запись видима и не вызывает сеть. Изменение spec/snapshot/clock/trace/record или незавершённый capture не активирует новый набор и даёт ненулевой код CLI. Нельзя считать authored проверку качеством модели или всей мультимодальной демонстрацией.

## Idempotence and Recovery

Прежние локальные изменения сохранены до работы, рабочие базы/секреты не перезаписываются. Никаких новых SQL-миграций или сбросов. Проверки создают уникальные каталоги в var; публикации хранят immutable наборы/историю и atomic pointer. После прерывания публикации прежний указатель остаётся; stale lock не снимать автоматически, использовать новый каталог или проверить процесс. Для сравнения сохраняются backup/приватные SHA-256.

## Artifacts and Notes

Полный pytest: 587 passed, 1 warning (50.64s). Новые 46 профильных cases прошли. Ручной HTTP/CLI/restart — var/server-completion-check-6bdd874251c3429f9616fd1cfc78dfe0/result.json; предыдущий прогон — var/server-completion-check-58c8279f14cb4d60b4e8b8a83dae9f65/result.json. Раздельные CLI record/verify: var/scenario-first-review (9 steps/11 records, одинаковый trace hash). Старые runtime/live503 и human workflow повторены: var/runtime-binding-check-52296e50face4834a0f5c0a13b0fee81/result.json и var/human-workflow-check-f6899277de6e4cce9516b2a8f63455e6/result.json. 16/7+proposal+14 workflow valid, 38 schemas, pip check успешен. Проверка сохранности/credential patterns — final-review.json рядом с исходным backup; credential findings=0, пять приватных файлов совпадают. Node/UI-проверки не выполнялись, frontend не менялся.

## Interfaces and Dependencies

Новые server DTO: TopicPeriodStats, PassportFields и PassportUpdate. Existing Topic/TopicPassport/ProposedTopic/ExtractedNeeds/BatchAssignment сохраняют model JSON schemas. Политика обработки v3 и prompts не меняются. Только стандартная библиотека и прежние зависимости; новый clock — internal context manager, не настройка пользователя/HTTP. Сценарный record/verify расширяет CLI, не запускает импорт fixture в обычном production API.

Редакция 2026-11-07: новый план фиксирует три явно порученные независимые серверные части и границу интеграции с участниками 1/4 и live-моделью.

Редакция 2026-11-07 после выполнения: отмечены реализованные A/B/C, аддитивный периодный DTO/примеры, provenance, atomic публикация, фактические 587 tests/реальные процессы и оставшиеся внешние интеграции.

Редакция 2026-11-07, предкоммитная: повторены полный pytest (587 passed, 1 warning, 53.16s), реальные HTTP/CLI/restart, примеры/pip check и preservation/secrets/model-schema review; обновлена авторизация публикации без merge.
