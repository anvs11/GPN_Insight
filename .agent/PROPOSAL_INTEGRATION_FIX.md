# Подключить proposal U2 и сохранить старые checkpoint

Продолжение по новому поручению пользователя, согласованному с U2: cold-start реализован отдельно по .agent/COLD_START.md, 541 tests и реальный HTTP/restart прошли. Прежняя граница «холодный старт не меняем» в этом документе описывает только завершённый предыдущий срез, не текущее ограничение проекта.

Этот ExecPlan поддерживается по .agent/PLANS.md. Работа выполняется участником 3 в основной рабочей папке, в существующей feat/server-text-integration после fast-forward до main 8f69c7d. Алгоритмы и промпты U2, холодный старт, импорт U1 и UI U4 не изменяются.

## Purpose / Big Picture

При полном поиске в непустом каталоге настоящий AnalysisService U2 должен передавать предложенную тему через проверенный gateway в SQLite. Повтор старого успешного назначения с legacy new_topic должен переиспользовать checkpoint и сохранять решение владельца. Ни один из этих случаев не подтверждает доступность live-модели.

## Progress

- [x] (2026-11-07) Повторены 528 тестов, примеры и pip check. В отдельной synthetic/authored проверке воспроизведены ANALYSIS_REQUEST_CONFLICT при proposal и PROPOSAL_PROFILE_MISSING при старом checkpoint; исходное решение осталось целым. Отчёт var/reviews/u2-6ad2358-89847f70f5734456991a00127163c7ad/result.json.
- [x] (2026-11-07) Чистая feature-ветка обновлена fast-forward до 8f69c7d; main и приватные данные не менялись.
- [x] (2026-11-07) Подключён одинаковый путь реального propose.md к профилю, проверяющей обёртке и AnalysisService; стандартный путь и явный override проверены.
- [x] (2026-11-07) Разделены требования provenance к ProposedTopic и старому new_topic; повтор checkpoint с отсутствующим proposed_topic и assign-only metadata проходит с прежним решением. Неверные хеши/модель/live-authored отвергаются.
- [x] (2026-11-07) 537 backend tests passed (9 новых), один прежний warning; 16/7+proposal+11workflow, pip check успешны. Реальные ordinary Uvicorn/replay/new→решение→delta match→повтор/restart/unknown пройдены; результаты внесены в ExecPlan и документацию.

## Surprises & Discoveries

Зелёный прогон не покрывал настоящее предложение темы через стандартную сборку. integration._prompts намеренно пропускал builtin propose.md, а AnalysisService читал его самостоятельно. Тест отсутствия промпта продолжал считать его отсутствующим даже после появления файла. Pipeline начал требовать propose metadata для любого new, хотя исторический new_topic сохранялся с одним ответом assign_topic.

## Decision Log

Решение (2026-11-07, участник 3): подключить путь промпта через необязательные аргументы существующей factory build_analysis. Это оставляет алгоритм U2 неизменным и обеспечивает совпадение содержимого с хешами профиля. Старый профиль не переписывать: профиль с добавленным prompt hash требует отдельно подготовленных точных записей.

Решение (2026-11-07): ProposedTopic требует metadata обеих операций assign_topic/propose_topic. Legacy new_topic сохраняет прежнее требование assign_topic; если в нём есть дополнительные proposal metadata, их также сверять с профилем. Режим live по-прежнему принимает только origin=model. Поддельные хеши и отсутствие обязательной операции не разрешать ради совместимости.

## Outcomes & Retrospective

Срез завершён: штатный и явный override промпта согласованы, legacy checkpoint переиспользуется без выдуманной операции, новый ProposedTopic сохраняет строгие metadata. 537 tests/примеры/pip check и настоящий HTTP/restart прошли. Пустой каталог по согласованию остаётся review: изменение политики принадлежит U2. Рабочие .env/SQLite, опубликованные записи и история не мигрировались и не переанализировались автоматически. Commit/push этим запросом не поручены.

## Context and Orientation

backend/app/ingestion/integration.py собирает профиль, TextNormalizer и проверенный AnalysisService. backend/app/adapters/text.py создаёт сервис U2. backend/app/pipeline.py сохраняет назначения и stage_checkpoints в SQLite v3. AssignmentResult содержит альтернативы proposed_topic и legacy new_topic. Исторические успешные шаги читаются по точному ключу входа/профиля, без повторного обращения к gateway.

## Plan of Work

Первый небольшой этап исправляет выбор builtin/sibling/override propose.md и передаёт тот же путь factory. Регрессии используют настоящий AnalysisService, synthetic gateway и точные authored replay; неизвестный запрос остаётся ошибкой.

Второй этап исправляет выбор обязательных операций для checkpoint и свежего назначения. Повтор legacy результата должен завершаться completed с прежними темой, решением и историей. Новый ProposedTopic без proposal metadata по-прежнему отклоняется без частичных записей.

Третий этап запускает ordinary Uvicorn с отдельной БД/профилем/replay, проверяет сохранение, повтор, рестарт и отказ неизвестного запроса. Итоги записываются в этот план, основной ExecPlan и документы стыка. Не менять зависимости или модельные response schemas.

## Concrete Steps

Из backend запустить ..\.venv\Scripts\python.exe -X utf8 -m pytest -q с test_topic_proposal.py/test_text_integration.py и новыми регрессиями, затем весь набор. Выполнить app.cli validate-examples, pip check и scripts.check_runtime_binding. Для нового proposal подготовить явно synthetic/authored запись и отдельную SQLite в var, не использовать рабочую базу.

## Validation and Acceptance

При непустом каталоге provisional_no_match вызывает propose_topic через одну проверенную сборку и сохраняет тему с server ID/new/v1/owner=null. Повтор и рестарт не создают дублей и не меняют решение. Пустой каталог остаётся review. Legacy checkpoint с прежним профилем и одним assign metadata переиспользуется, не вызывает анализ и не требует придуманных proposal metadata. Недостоверное происхождение и неизвестные replay-запросы отклоняются явно.

## Idempotence and Recovery

Все сценарии создают отдельные уникальные каталоги в var и останавливают только собственные серверные процессы. Не удалять и не исправлять существующие checkpoint/записи/базы. Изменения схем SQL и миграции не нужны. Повторять проверки безопасно.

## Artifacts and Notes

Исходный отчёт ревью указан в Progress. Итоговый HTTP-отчёт: var/proposal-binding-check-9e441bffc278433d815bcc16b23ca10f/result.json. Проверены actual TextNormalizer/AnalysisService/ReplayGateway без tests adapter в сервере, count2, 409, restart/6 reused, REPLAY_RECORD_MISSING, integrity/FK, сохранность приватных файлов и профиля/записей. Старый runtime/live503 сценарий: var/runtime-binding-check-f8423322d0374125bb6cbec8c75bc3d0/result.json. Воспроизведение автоматической части: из backend pytest -q tests/test_proposal_binding.py (9 passed); полного набора — pytest -q (537 passed).

## Interfaces and Dependencies

Сохраняются ProposedTopic, AssignmentResult, HTTP DTO, SQLite v3 и TextGateway.generate. build_analysis получает необязательные Path для assign/propose; прежние вызовы остаются валидными. Сетевой gateway не создаётся. Зависимости и prompt contents не меняются.

Редакция 2026-11-07: план фиксирует согласованный серверный срез после ревью U2, отделяя его от принадлежащего U2 холодного старта.

Редакция 2026-11-07 после выполнения: отмечены реальные 537 tests и HTTP/restart-результаты, обновлена передача U2; scope и алгоритмы владельцев сохранены.

Дополнительная регрессия после полного прогона: scripts.check_pipeline подтвердил реальный interrupted/reuse (var/pipeline-check-c17914e1bea24ccf99b879b5fe67d95a/result.json), scripts.check_human_workflow подтвердил решения/review/merge/exclude/CLI/restart (var/human-workflow-check-f0ef181b7f564e08a0f61694257c68aa/result.json). Оба успешны, модели не вызывались.
