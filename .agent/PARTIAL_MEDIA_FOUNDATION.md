# Контракты partial, synthetic safety и media Normalizer/replay

Последующее A4–A7 подключение выполнено по `.agent/PARTIAL_WEEKLY_INTEGRATION.md`. Этот документ сохраняет scope/приёмку A1–A3; его граница «A4 ещё впереди» историческая. Текущий production partial/replay интерфейс описан в docs/PARTIAL_WEEKLY_HANDOFF.md.

Живой ExecPlan по `.agent/PLANS.md`, дополняющий `.agent/EXECPLAN.md` и задачи A1–A3 из docs/NEXT_IMPLEMENTATION_TASKS.md. Пользователь 09.11.2026 поручил реализовать эти три части; включение media в production Pipeline (A4), классификация/weekly и binary API не входят в этот срез.

## Purpose / Big Picture

Подготовить проверяемый результат нормализации, который сохраняет пригодные фрагменты и ошибки отдельных вложений, точные coverage/revision и аналитический digest. Добавить честный воспроизводимый ASR/VL replay без сети и без фиктивного live, а также устранить выдачу nonsynthetic оригиналов из HTTP источников/вложенных карточек, сохраняя SQLite.

## Progress

- [x] (2026-11-09) Проверены состояние ветки и утверждённый A1–A3 scope; сохранены прежние незакоммиченные документы, private SHA и fingerprints legacy model schemas в var/reviews/partial-media-foundation-20261109/before.json.
- [x] (2026-11-09) A1: processing_schemas.py, строгие material/source units и coverage/error/evidence invariants, semantic revision/analysis digest, 8 JSON-примеров и 11 новых exported schemas (49 суммарно). Legacy model schemas/request hashes неизменны; SQL revision publication остаётся A4.
- [x] (2026-11-09) A2: единый public projector, additive original_access=available|restricted, raw originals сохраняются в SQLite; synthetic safety fixtures и API/text replay/NeedExtractor regression. MergePreview также проецируется: контрольный тест воспроизвёл утечку до исправления, после прошёл. Производные fragments/comments не объявляются полностью обезличенными.
- [x] (2026-11-09) A3: явный media profile/request/immutable recordings, маскирование до записи, safe reread и реальные duration bounds, отдельный partial Normalizer, independent success cache и видимые failures; live отвергается. Production bootstrap/Pipeline не менялись.
- [x] (2026-11-09) Финальный backend 728 passed/4 Windows symlink skipped/1 прежний warning (49.37s), frontend 258 tests/build, 8 processing и прежние 16/7+proposal+14workflow примеры, export49 и pip check прошли. Ручной partial/retry/reopen: var/partial-media-check-792863700b744ca9bf54669686c4bd6d/result.json. Старые text HTTP/CLI/restart: var/server-completion-check-40412c75bd96472fb727466040caf1f6/result.json; manual workflow/restart: var/human-workflow-check-3a9adaff4baa44b78f433997826d8c3b/result.json. Финальный audit var/reviews/partial-media-foundation-20261109/result.json подтвердил private5, source/archive, legacy7 и нетронутые SQL/prompts/pins/UI components; diff/credential scan чисты.

## Context and Orientation

Legacy text: schemas.NormalizedDocument и gateways.MediaText/ApprovedAsset неизменны. Pipeline сейчас не принимает media/ошибочный документ; этот guard сохраняется. Storage запрещает изменение опубликованного результата того же источника: в A1 вводится контракт semantic revision/analysis digest, но миграция/публикация revision в SQLite будет A4. Не выдавать JSON-envelope за готовый механизм supersession/пересчёта связей.

## Plan of Work

backend/app/processing_schemas.py определяет новые отдельные DTO: исходный контекст/исходы материалов, покрытие с явными единицами, MediaProcessingProfile/запрос/запись и NormalizationResult/AnalysisInput. Результат связывает source/version/hash, profile, материал, фрагменты, errors, provenance и revision. Аналитический вход выделяется только через проверенную проекцию success fragments и сохраняет source_errors/coverage/revision вне model-facing legacy document. Самостоятельный AnalysisInput ещё не подключает U2.

backend/app/media_replay.py задаёт semantic key без absolute_path, точную схему/профиль/модель/parameters/recipe, immutable явно выбранный набор без active fallback. Записи предварительно маскируются, повторная загрузка проверяет schema/hash/metadata и masked response; неизвестное — REPLAY_RECORD_MISSING. Публикация нового файла атомарна и exclusive, старое содержимое не заменяется. Сетевых адаптеров нет.

backend/app/ingestion/media_normalizer.py использует persistent resolver, проверяет контекст и реально доступный registered файл перед cache/gateway, сохраняет только валидные masked success checkpoints в отдельном explicit каталоге var. Ошибки не кэшируются как успех. Без текста и без usable media analysis input отсутствует. Повтор с восстановленным материалом меняет semantic revision/digest, сохраняя stable IDs уже успешных фрагментов; старые text IDs остаются прежними. Unit и локальная процедура доказывают это до подключения A4.

SourceDetail получает аддитивный original_access=available|restricted (available для внутренних legacy объектов). Публичный projector ставит restricted для nonsynthetic и удаляет ticket.text/requester_id/original URL и имя файла, сохраняя идентификаторы/даты и явно помечая ограничение. Это политика выдачи originals, не обещание полного обезличивания производных фрагментов/ручных комментариев; реальные данные до corporate access policy не подключать. Нормализованные источники остаются ограниченными текущим masking и предупреждениями. API topic/list/decision/passport применяют один projector. Не менять raw repository/get_source и входы pipeline; UI уже скрывает nonsynthetic original, добавить optional type для согласованного read поля без новой формы.

## Validation and Acceptance

Тесты: coverage mismatch/чужой asset/fragment/error/forged revision отвергаются; старые schemas/hashes/recordings и SQL неизменны. Full/partial/failed/empty различимы; анализ успешного контекста не скрывает исходные errors. Replay отвергает неизвестные bytes/model/schema/operation/parameters/set и malformed/unsafe записи, no network. Четыре формата synthetic, timestamps по реальной duration, подмена файла, cache corruption, reopen/retry и stable fragment IDs проверяются.

A2 проверяет raw original sentinel через source/list/card/decision/passport и SQLite after reopen, без source text в безопасной ошибке/отчёте. PII_SAMPLE/UPLOAD_BAD/INJ_TEXT маркированы synthetic, hostile инструкции не исполняются сервером; authored не доказывает устойчивость настоящей модели. API/примеры/TypeScript совместимы.

Из backend в PowerShell выполнить профильные tests, затем ..\.venv\Scripts\python.exe -X utf8 -m pytest -q -rs, app.cli validate-examples и scripts.check_partial_media_foundation; frontend test/build при изменении read DTO. Linux/macOS: ../.venv/bin/python. Отдельные процедуры используют новые var folders, не рабочую SQLite.

## Milestones and Concrete Steps

A1 задаёт проверяемую границу результата: revision — SHA неизменного результата, digest — SHA пригодного входа анализа, coverage — программные счётчики с явной единицей. В backend выполнить следующую команду; ожидается PASS с восемью примерами и одиннадцатью новыми схемами, без БД и моделей:

    ..\.venv\Scripts\python.exe -X utf8 -m scripts.check_processing_contracts

A2 закрывает выдачу originals на сервере и добавляет синтетическую проверку маскирования, вредных имён загрузок и инструкций внутри текста. Из backend выполнить следующие тесты; raw sentinel не должен появиться в HTTP ответах, а исходная SQLite должна сохраниться:

    ..\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_safety_foundation.py

A3 связывает зарегистрированные файлы с точными authored ответами и независимыми успешными checkpoints (сохранёнными результатами отдельных материалов). Из backend выполнить процедуру ниже. Она создаёт только отдельный var-каталог; ожидаются partial 4/5, затем retry/reopen 5/5 и gateway calls=3+1. Повтор не меняет старую БД и не вызывает сеть:

    ..\.venv\Scripts\python.exe -X utf8 -m scripts.check_partial_media_foundation

После трёх этапов полный pytest даёт 728 passed и четыре Windows symlink skips; известный warning относится к deprecated AnyIO alias в Starlette TestClient. Проверка живой модели и публикация revisions в production SQLite в эти результаты не входят.

## Interfaces and Dependencies

Новые контракты отдельно от schemas.py legacy model-facing классов; один read SourceDetail field допустим. SQL/import/source_content_hash/prompts/U2 алгоритмы/Node и Python pins не меняются. Resolver/gateways интерфейсы используются, а не дублируются. Новые пакеты не нужны. Media Normalizer возвращает NormalizationResult, не маскируется под старый PipelineAdapters.Normalizer до A4.

## Idempotence and Recovery

Наборы/успешные checkpoints immutable и bound exact semantic key; разные содержимое/версии не перезаписываются. Ошибки публикации не активируют частичный файл; новый retry использует прежние валидные success. Прежние DB/results/private файлы защищены SHA. При повреждении cache выдаётся явная ошибка, запись не удаляется автоматически. Нет общей SQLite-транзакции с файлами. Commit/push не поручены.

## Surprises & Discoveries

MaterialError сейчас блокирует весь документ в legacy Pipeline/CheckedAnalysis. Убирать это до revision publication нельзя: immutable normalized_documents/needs и старый extraction cache key не умеют безопасно дополнить частичный контекст. Поэтому этот этап готовит самостоятельную версионированную границу, сохраняя guard до A4.

## Decision Log

09.11.2026, U3: реализовать A1–A3 отдельными модулями, не менять алгоритмы У2 или включать media endpoint в отсутствие A4. Для read safety добавить явный additive original_access и единый public projector; raw repository и evidence storage не переписываются. Профили/recordings используют только явно synthetic/authored demo IDs при отсутствии реального сервиса.

## Outcomes & Retrospective

A1 partial/media contracts, A2 safety/read policy и A3 standalone Normalizer/replay завершены. В реальной isolated процедуре один повреждённый PNG даёт partial 4/5 с пригодным контекстом/видимой ошибкой; после восстановления выполнен только оставшийся gateway (3+1), старые fragments/revision snapshot валидны, новый context меняет digest, SQLite/решение/история не меняются. Последующий повтор не вызывает gateways. Это чтение authored записей, не распознавание живой модели. Следующий этап — A4 SQLite revision publication/coverage/progress и штатный bootstrap; production media, категория/приоритет, weekly и binary API этим срезом не выполнены. Commit/push не выполнялись.

Обновление 09.11.2026: завершены A1–A3 и повторная полная проверка; источник data_origin отделён от recording origin. Протокол gateway ошибок возвращает безопасные явные коды. Итоговые артефакты и оставшаяся A4 граница уточнены по фактическим запускам.
