# Создать первую тему через проверенный ProposedTopic

Этот ExecPlan поддерживается по .agent/PLANS.md и дополняет основной .agent/EXECPLAN.md и .agent/PROPOSAL_INTEGRATION_FIX.md. Работа выполняется в существующей feat/server-text-integration поверх сохранённых локальных серверных исправлений. Пользователь 2026-11-07 сообщил о согласовании с U2 и поручил нам выполнить его часть холодного старта.

## Purpose / Big Picture

На пустой SQLite первая подтверждённая потребность должна приводить к созданию темы через настоящий AnalysisService, а следующее похожее обращение — присоединяться к ней. Холодный старт означает отсутствие тем, а не отсутствие источников. Ошибка proposal не должна превращаться в успешную карточку или потерю сохранённых этапов.

## Progress

- [x] (2026-11-07) Проверены чистота границ текущих локальных изменений и существующий запрет proposal при пустом каталоге. Ранее выполнены 537 backend tests и реальный warm HTTP/replay/restart.
- [x] (2026-11-07) Общий propose_topic разрешён после явного provisional_no_match на пустом каталоге. Review/evidence/provenance и server IDs сохранены, prompts/HTTP/SQL не менялись.
- [x] (2026-11-07) Политика обновлена до v3, fixtures используют новые версии наборов. Проверены cold/review/forged evidence, точный отсутствующий proposal record без частичной темы, явный retry с reuse extraction и cold/warm new→match/reopen.
- [x] (2026-11-07) 541 backend tests passed, один прежний warning; 63 профильных tests, 16/7+proposal+11workflow и pip check успешны. Настоящий ordinary Uvicorn/HTTP/restart на пустом каталоге прошёл; короткая передача U2 в docs/TEAM_INTEGRATION.md. Commit/push не выполнялись.

## Surprises & Discoveries

Сервис уже отправляет один assign_topic с пустым topics и получает проверенные metadata. Для cold-start отсутствует только разрешение следующего propose_topic: условие дополнительно требует непустой каталог. Старые tests и synthetic gateway намеренно закрепляли review, поэтому вместе с кодом нужно обновить только изменившиеся ожидания и явно authored-ответ proposal.

Итог проверки: отсутствующий точный proposal record даёт completed_with_errors/REPLAY_RECORD_MISSING, тема и assignment checkpoint отсутствуют. После явного подключения полного тестового набора повтор даёт completed/reused_steps=2/completed_steps=1. Само приложение записи не дополняет и fallback не выполняет.

## Decision Log

Решение (2026-11-07, пользователь/U3 по согласованию с U2): использовать существующие два шага assign_topic → propose_topic. Пустой каталог считается полностью проверенным, но первая тема создаётся только при явном provisional_no_match без кандидатов. Review модели остаётся review. Схемы, промпты и HTTP не менять.

Решение (2026-11-07): увеличить INTEGRATION_VERSION с u2-text-integration-v2 до u2-text-integration-v3. Политика cold-start изменилась, поэтому точный профиль/checkpoint/replay не должен скрыто использовать прежний cached review. Старые профили/записи/БД не переписывать: обновлённый runtime требует новый проверенный профиль/набор. Legacy checkpoint по своему точному профилю остаётся читаемым Pipeline.

## Outcomes & Retrospective

Холодный старт завершён с согласия пользователя/U2. На пустой базе первая тема создаётся проверенным proposal, второе обращение совпадает с ней, решение/история сохраняются после повторного импорта/restart. Все 541 tests и реальные HTTP/replay/regression проверки прошли; рабочие приватные данные сохранены. Live-модель, независимые метрики качества, медиа и UI не выполнялись. Прежние локальные изменения сохранены. Commit/push не поручены.

## Context and Orientation

backend/app/analysis/service.py вычисляет полноту поиска, проверяет BatchAssignment, вызывает propose.md и возвращает AssignmentResult. Pipeline создаёт Topic с deterministic ID, new/v1/owner=null, проверяет evidence/metadata и сохраняет assignment checkpoint атомарно. integration.py строит профиль; новая версия политики попадает в точные ключи. scripts/text_pipeline_fixture.py создаёт только authored тестовые ответы и никогда не подключается к обычному серверу.

## Plan of Work

Первый небольшой этап убирает требование непустого каталога из общего разрешённого proposal-пути и уточняет комментарий. Прямой сервисный тест проверяет new с двумя metadata, review без proposal, отказ выдуманного evidence.

Второй этап обновляет явно synthetic fixtures для подготовки точного replay и проверяет пустую БД через обычный create_app без передачи PipelineAdapters. Ошибка/неизвестная запись proposal не создаёт тему/assignment checkpoint; повтор переиспользует нормализацию/extraction. Успех сохраняет тему/решение/историю и присоединяет второй источник, после reopen все этапы переиспользуются.

Третий этап повторяет полный pytest и настоящий Uvicorn/HTTP с отдельными profile/SQLite/recordings в var. Обновить ExecPlan, текущие ограничения и текст для U2, различая authored replay и качество живой модели.

## Concrete Steps

Из backend выполнять ..\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_analysis_service.py tests/test_text_integration.py tests/test_text_pipeline.py tests/test_proposal_binding.py, затем полный pytest -q. Выполнить app.cli validate-examples, pip check и scripts.check_runtime_binding. Ручная HTTP-проверка поднимает только собственный Uvicorn на свободном localhost-порту и закрывает его после restart-проверки.

## Validation and Acceptance

В первом assign-запросе topics=[], total_topic_count=searched_topic_count=0, passport_versions={}; после provisional_no_match следует propose_topic. Итог new имеет metadata обеих операций и точные evidence. Вторая потребность совпадает с первой темой. Статистика не удваивает source_id, решение/история сохраняются после повторного импорта и рестарта. Review/ошибка/неизвестный replay не создают ложного успеха. SQL/HTTP/response schemas и секреты неизменны.

## Idempotence and Recovery

Все fixtures/БД/записи создаются отдельно в уникальном var-каталоге. Рабочая .env/SQLite и опубликованные replay не изменяются. Новая политика не переинтерпретирует успешные исторические checkpoint в их прежнем профиле. Не восстанавливать старый cached review путём удаления данных; готовить новый профиль/набор согласно правилам проекта.

## Artifacts and Notes

Cold HTTP: var/cold-start-check-599ed3c6fbf84876b6f3814d7ba8c1d7/result.json. Подтверждены empty catalog, ordinary app.main:app, actual Normalizer/AnalysisService, strict authored replay, new→match/count2, 409, restart/6 reused, unknown error, integrity/FK и сохранность .env/рабочих SQLite/profile/recordings. Warm runtime/live503: var/runtime-binding-check-58bec387eaed451da19941e30729cf9f/result.json. Реальный interrupted/reuse: var/pipeline-check-53a9d2b66116468ea42b4133331f376d/result.json. Полный pytest: 541 passed, один известный Starlette/AnyIO warning (28.46s).

## Interfaces and Dependencies

Переиспользуются AnalysisService, TextGateway, ProposedTopic/AssignmentResult, Pipeline, SQLite v3 и strict replay. Новые HTTP-поля, SQL-миграции, зависимости и модельные prompts не требуются. Готовый текст передачи U2 будет в docs/TEAM_INTEGRATION.md.

Редакция 2026-11-07: с согласия пользователя расширена прежняя граница U3 на согласованную с U2 реализацию холодного старта; остальная зона U2 не расширяется.

Редакция 2026-11-07 после выполнения: отмечены реальные 541 tests/63 профильных, HTTP/restart, missing-record/retry и отчёты; ограничения live/качества сохранены.
