# Текстовый импорт и интеграция участников 1/2/3

Фактический результат на 2026-11-06: U1-02/U1-03 перенесены на main 2afa431 (PR #7 участника 2). Рабочая ветка feat/u1-text-integration. Реализация участника 2, её промпты, общие DTO, SQLite/storage, pipeline/worker, main.py и зависимости сохранены. Это интегрированный текстовый срез, не завершение HTTP-загрузки/медиа/live/new.

## Готовые компоненты

app.ingestion.importer.import_tickets принимает UTF-8 bytes/str manifest. dry_run=True повторно использует строгий общий валидатор и read-only SQLite counts, не создаёт/мигрирует БД и не пишет журнал. dry_run=False полностью валидирует вход, захватывает существующий repo.import_commit до регистрации, передаёт DatasetRegistration с materials=[], origin=imported и честным synthetic в register_dataset. Серверный UUID идентифицирует запрос; точный повтор с тем же UUID возвращает прежнюю квитанцию. Финальные counts из ImportResult, успех после commit; потеря ответа не отменяет импорт. Внешняя активная DB-транзакция, RUN_ACTIVE/IMPORT_ACTIVE и ошибочная/отсутствующая БД не обходятся. Любые media/files отклоняются целиком. Четыре data/demo/manifest.json и старые data/contracts не менялись.

app.masking.mask_pii и app.ingestion.normalizer.TextNormalizer(profile) сохраняют исходник и pipeline-контекст. Product/source берутся из TicketInput, content_hash/source_version — из аргументов pipeline, version=source_version. Маскирование не вычисляет новый source_content_hash и не читает БД/файлы, не пишет документ/needs/checkpoints и не вызывает модели. Локальный text-1 заменяется стабильным fragment_id pipeline. Ошибки — MaterialError либо безопасный код некорректного контекста, не пустой успешный анализ. Документ/checkpoint сохраняет pipeline в своей транзакции, защиты NORMALIZATION_CONFLICT/ANALYSIS_CONFLICT остаются действующими. Детали — [NORMALIZATION.md](NORMALIZATION.md).

## Явное подключение вместо копирования чужого анализа

app.ingestion.integration предоставляет две функции:

    build_text_profile(settings, *, model_id, recording_set_version,
                       parameters=None, max_attempts=2) -> ProcessingProfile
    build_text_pipeline(gateway, profile, settings) -> PipelineAdapters

Первый helper создаёт существующий ProcessingProfile из фактической версии/recipe нормализации, хешей двух реальных промптов и лимитов settings. Model ID и recording_set_version обязательны, их не берут из manifest и не выдумывают по умолчанию. Parameters копируются; normalization_hash/text_integration задаёт сборка. text_integration содержит версию проверок u1-text-integration-v1, бюджет попыток 1–3 и retrieval/batch лимиты; политика входит в checkpoint/replay key. Смена политики требует новой версии и проверенного профиля. Полный профиль фиксируется до run.

Второй helper создаёт настоящий TextNormalizer и настоящий build_analysis/AnalysisService из PR #7. Частные wrapper-классы проверяют границу вызова, не подменяя алгоритмы extraction/retrieval/assignment, schema или metadata. Они не создают потребности/темы и не вычисляют бизнес-статистику. Дополнительных SQL, DTO, guard и зависимостей нет.

Подключение в существующую точку участника 3:

    adapters = build_text_pipeline(gateway, profile, settings)
    app = create_app(settings, pipeline_adapters=adapters)

Здесь gateway/profile/settings уже должны быть получены из утверждённой серверной конфигурации. Для replay это ReplayGateway с точным набором/model_id/NORMALIZATION_HASH; для live нужен настоящий внутренний адаптер с model metadata. Наличие фабрики не создаёт доступ к Qwen. Первоначально этот срез не добавлял автоматическую сборку; теперь обычный main.app подключается по явному APP_PROCESSING_PROFILE_PATH через проверенный bootstrap (docs/RUNTIME_PROFILE.md). Без полной согласованной конфигурации сохраняются 503 INTEGRATION_NOT_READY и отсутствие нового run; fixture gateway не подставляется. В проверенной явной сборке существующие POST/GET runs, sources и PATCH decision работают с настоящими компонентами через FastAPI.

## Проверки границы

До gateway ошибочный NormalizedDocument отклоняется с EXTRACTION_MATERIAL_ERRORS; неподходящий продукт/normalization_version — NORMALIZATION_PROFILE_CONFLICT. После извлечения source_id и fragment_id сверяются с входом, evidence_quote должна дословно входить в соответствующий masked-фрагмент. Ошибка всей выдачи не исправляется молча и не публикуется частично. Pending need_id коллеги остаются локальными, окончательные ID назначает pipeline.

Gateway получает копию реального TextRequest из AnalysisService. Проверяются схема, неизменные prompt/prompt_version и весь profile.parameters. Metadata создаёт только gateway: request_hash через общий request_key, prompt_hash, normalization_hash, model_id, recording_set_version, JSON-типы parameters, допустимый origin проверяются до возврата. False не равен JSON-числу 0. ReplayGateway явно запрещён в режиме live (ANALYSIS_MODE_CONFLICT), даже если запись имеет origin=model. В live принимается только origin=model; скрытого переключения в replay нет. В replay model/authored видны в Run. Неизвестный запрос REPLAY_RECORD_MISSING не повторяется и не превращается в пустой успех.

Тайм-аут и некорректная структурированная выдача имеют явные ANALYSIS_TIMEOUT/ANALYSIS_RESPONSE_INVALID после не более max_attempts вызовов. Сбой произвольного gateway даёт безопасное сообщение без оригинала/исключения/секрета. Metadata успешного результата — от реального успешного gateway-вызова; фиктивные поля не добавляются. Ошибки происхождения, ссылок и цитат не маскируются retry.

У PR #7 оценка пакета меньше полного Topic JSON. Граница перед вызовом модели проверяет длину канонического JSON реально переданного массива topics против app_passport_batch_max_chars. Слишком большой запрос даёт ANALYSIS_BATCH_TOO_LARGE до вызова gateway; он не обрезается и не отправляется частично. Это лимит символов topics JSON, не измеренный лимит токенов всей модели: prompt/need/schema и транспортный бюджет ещё требуют проверки внутреннего API. Автоматическое перепаковывание полного Topic остаётся участнику 2. Отсутствие допустимых пакетов без metadata — ANALYSIS_PROVENANCE_MISSING.

Эти гарантии относятся к build_text_pipeline. Прямой вызов необёрнутого AnalysisService не получает новых проверок: владельцам 2/3 передаётся фабрика целиком. Новый сервис сохраняет свою политику cold-start: review/new_topic=None, автоматического ProposedTopic/new пока нет. Это ограничение не обходится тестовой моделью или незаметным добавлением authored-темы в обычный сервер.

## Синтетический пример и проверенные результаты

data/contracts/normalization-handoff.json — новый явно synthetic/authored пример U1. Он использует реальные хеши extract-needs-v1/assign-topic-v1, текущий normalization_hash и полный profile.parameters новой сборки. Локальный исходный пример в старой рабочей копии не переписан. Старые контрактные JSON/replay клона сохранены; для новой интеграции нужны точные новые записи с новой recording_set_version.

На macOS/Python 3.13.0 выполнены 369 backend-тестов, одно прежнее предупреждение Starlette/AnyIO. Старые 18 тестов и все 14 файлов PR #7 сохранены. Пройдены 16 общих/7 HTTP-примеров, pip check и ручные процедуры preview/import/normalizer/pipeline. Windows, живая Qwen и браузерный путь в этой задаче не запускались. Никакие опубликованные записи/решения/рабочая БД не заменялись.

scripts.check_text_pipeline сначала на отдельной capture.db подготавливает **явно authored** ответы для четырёх известных synthetic обращений через настоящие Normalizer/AnalysisService/Pipeline. Это не модель. Затем на новой verify.db запускает реальный FastAPI/RunService/Pipeline с настоящим strict ReplayGateway и теми же компонентами. Тестовые TestComponents из tests/pipeline_adapters.py не используются. Дополнительная authored-тема для warm-start создаётся только этой диагностикой отдельно от существующего seed-demo; она исключена из анализируемого октябрьского периода и честно даёт пятый тикет в статистике.

Фактически проверены API 202 → completed, masked-источники, ручное решение, повтор 4 unchanged с 12 reused checkpoints/0 completed, обновление 1/3 и явный REPLAY_RECORD_MISSING для неизвестного текста, сохранность прежней версии источника/ID/решения/истории после reopen. При delta статистика активных обращений становится 4 (seed + 3 текущих), один источник виден pending; решение не исчезает. Отдельные тесты проверяют cold-start с четырьмя review без новых тем и resume после сбоя gateway с настоящими компонентами. Нормализационная ошибка не вызывает extraction gateway и не публикует пустой успех.

Фактический ручной отчёт: var/text-pipeline-check-495332bb7f24467c92cc66e6e0b366a9/result.json. Отчёты относятся к новому клону и не включаются в Git.

## Повторение

Из backend самостоятельного клона со своим .venv:

    ../.venv/bin/python -m pytest -q
    ../.venv/bin/python -m scripts.check_text_pipeline
    ../.venv/bin/python -m scripts.check_text_normalizer
    ../.venv/bin/python -m scripts.check_text_import
    ../.venv/bin/python -m scripts.check_import_preview

PowerShell: вместо ../.venv/bin/python использовать ..\.venv\Scripts\python.exe -X utf8. Окружение каждый создаёт на своей машине по requirements.txt. В подготовленном вложенном клоне этой сессии использован существующий Python той же машины: из backend путь ../../../../.venv/bin/python. .venv не копировалось; установка новых пакетов не выполнялась.

Ожидаются 369 passed, 1 warning и, среди строк check_text_pipeline:

    api: 202 -> completed; actual_normalizer=true; actual_analysis_service=true; origins=authored
    repeat: unchanged=4; reused_steps=12; completed_steps=0; count/decision/history=unchanged
    delta: updated=1 unchanged=3; unknown replay=REPLAY_RECORD_MISSING; empty_success=false
    PASS: real text pipeline/API and strict replay; no models or working database

UUID/время отличаются. Каждый запуск создаёт новый каталог var/, прежние результаты не удаляет.

Следующий за этим срезом текстовый HTTP/CLI-import с лимитами тела/частей уже выполнен отдельной задачей: docs/IMPORT_HTTP.md (64/433 теста и настоящий localhost HTTP/CLI/restart). Потом безопасные файлы, восстановление и ASR/VL. Они не добавлены в эту адаптацию. Главная передача участнику 3: build_text_profile/build_text_pipeline, профиль/точные recordings и эти команды; участнику 2 — сохранённые алгоритмы и открытые ограничения new/реального размера пакетов.

Последующее подключение обычного сервера выполнено отдельно: docs/RUNTIME_PROFILE.md. Реальные рабочие параметры пока не предоставлены по решению пользователя, поэтому по умолчанию готовность false. 26 runtime-тестов/459 backend и настоящий app.main:app на изолированном synthetic/authored replay прошли; все команды выше повторены. Live не включён, старые записи не переписывались.
