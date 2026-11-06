# Участник 1: текущий план GPN Insight

Обновлено 2026-11-06 после адаптации к main 2afa431 / PR #7 участника 2. Рабочая ветка feat/u1-text-integration. Основной план .agent/EXECPLAN.md; интерфейсы и проверенный общий путь — [docs/TEXT_INTEGRATION.md](docs/TEXT_INTEGRATION.md), правила текста — [docs/NORMALIZATION.md](docs/NORMALIZATION.md). Запросы ниже выполняются по одному после отдельного поручения; следующий этап автоматически не запускается.

## Что уже завершено

Текстовый preview (прежний шаг 2), фиксация через import_commit/register_dataset (шаг 3), masking-v2/TextNormalizer (шаг 4) перенесены из сохранённой локальной работы. Новая интеграция build_text_profile/build_text_pipeline использует настоящий AnalysisService участника 2 и существующие PipelineAdapters/create_app участника 3. Его 14 файлов/промпты и общие DTO/storage/pipeline/main не менялись. 369 backend-тестов и ручной строгий authored-replay/API прошли; модели не вызывались.

Проверены импорт 4 inserted → повтор 4 unchanged → изменение 1 updated/3 unchanged, rollback, source ID/версии/решение/история; Normalizer сохраняет исходник и переданный контекст без записи/моделей; итоговые ID/checkpoints назначает pipeline. Реальный общий API-путь с явными компонентами прошёл 202 → completed → источник → решение → повтор/reopen; повтор использует 12 checkpoints. Сбой gateway восстанавливается, неизвестный replay виден ошибкой, ошибочный материал не становится пустым успехом. У cold-start честно review, автоматического new/ProposedTopic у участника 2 ещё нет.

Исходная грязная копия docs/participant-2-start сохранена отдельно; новая работа выполняется в независимом клоне. Никаких pull/switch/reset поверх неё, скрытого переноса кода analysis/, старых recordings, .venv или БД. Первоначальный снимок — var/backups/pre-github-sync-20b27cfe16 исходной копии; снимок перед адаптацией — var/reviews/pre-u1-integration-2dd0dd2fda нового клона.

## Мои файлы и текущий стык

Участник 1: ingestion/importer.py, ingestion/normalizer.py, masking.py, ingestion/integration.py, проверки текста/маскирования/интеграции, synthetic data и docs/EFFECT.md. Участник 2 владеет analysis/, retrieval.py, prompts/, adapters/text.py, review.py и evaluation.py. Участник 3 — schemas.py, storage/repositories, pipeline/worker, main.py/cli.py/gateways/replay, общие API и зависимости. Участник 4 — UI/API-слой интерфейса и демонстрация.

Согласованные normalization_version=text-pii-v1-proposal и semantic_hash(normalization_recipe) сохранены; текущий NORMALIZATION_HASH брать из реализации, не из старого примера. Текущие версии промптов extract-needs-v1/assign-topic-v1, их реальные хеши и весь profile.parameters учитывает build_text_profile. Factory build_text_pipeline добавляет проверку ошибок/цитат/metadata/лимита topics JSON и конечных повторов, сохраняя алгоритмы U2. Прямой необёрнутый AnalysisService этих гарантий не получает: участнику 3 передавать сборку целиком, а не только класс.

Существующие DTO/guard/UUID-регистрация/read-only counts готовы, новых создавать не нужно. Guard до внешней DB-транзакции; файлы остаются вне SQLite-транзакции. Normalizer не сохраняет document/needs/checkpoints и не обходит NORMALIZATION_CONFLICT/ANALYSIS_CONFLICT. Medиа пока отклоняются целиком, seed-demo остаётся authored.

## Перед началом следующего этапа

Показать cwd/Git root/branch/HEAD/status, прочитать AGENTS.md, .agent/PLANS.md, ExecPlan и актуальные CONCEPT/STORAGE/PIPELINE/TEXT_INTEGRATION. При несовпадении проекта остановиться. Сохранить чужие/незавершённые правки; не переключать грязную копию и не переносить их молча. Для нового кода использовать короткую отдельную ветку от согласованной актуальной основы после сохранения текущей задачи. Общие DTO/HTTP/зависимости и main/cli сверять с участником 3. Не выполнять commit/push/merge без поручения.

## Следующий небольшой этап: прежний шаг 5, текстовый HTTP/CLI-import

> Подключи готовый import_tickets общим сервисом через согласованные CLI/HTTP-точки. С участником 3 сверить imports_api.py/main.py/cli.py и python-multipart. До multipart-разбора ограничь всё тело, затем manifest/число частей/полей и потоковое чтение; лишние files и media в текущем текстовом срезе отклоняй целиком. Preview ничего не пишет, commit повторно полностью валидирует вход и возвращает успех после commit с фактическими ImportResult counts. Не добавляй SQL/новый guard/DTO, не вызывай startup/init-db/модели ради регистрации. Передай участнику 4 существующие ImportPreview/ImportResult и русские ошибки 422/413/409. Проверь реальные HTTP preview/commit/repeat, неправильный multipart/слишком большой ввод, RUN_ACTIVE/IMPORT_ACTIVE и сохранность решений. INTEGRATION_NOT_READY у неподключённого анализа не должен скрываться или мешать независимому импорту текста. Обнови Progress фактическими результатами.

Готовность: одни валидатор/сервис для Python/CLI/HTTP, ограничения тела работают до дорогого разбора, нет частичного импорта и подтверждения до commit. UI не объявлять готовым до подключения участником 4.

## Отдельная передача подключения участнику 3

> Используй build_text_profile с реальными model_id/recording_set_version/parameters и settings; затем build_text_pipeline(gateway, profile, settings) и create_app(..., pipeline_adapters=adapters). Сверь утверждённую конфигурацию/профиль, настоящую точку создания TextGateway и режим. Не подставляй AuthoredFixtureGateway/TestComponents в обычный сервер и не выдавай authored/replay за живую Qwen. Для replay нужны точные новые записи текущих промптов/нормализации/parameters; старые наборы не переписывать. До появления внутреннего live-адаптера сохраняй явную неготовность. Пройди общий маршрут командами TEXT_INTEGRATION, сохрани unknown replay/material/provenance ошибки и checkpoints/ручные решения.

По умолчанию глобальный main.app не получает этих зависимостей, поэтому 503 INTEGRATION_NOT_READY остаётся честным. Явная сборка настоящих компонентов уже проверена на существующем API. New/ProposedTopic, перепаковывание полного Topic и токенный бюджет модели остаются участнику 2/владельцу gateway; не реализовывать их в Normalizer и не ослаблять защиту для зелёных тестов.

## Затем безопасные медиа и восстановление

> Реализуй security.py и файловый этап importer по Security ExecPlan: допустимые имена/пути относительно разрешённого набора, сигнатуры/расширения/MIME, размеры/длительность/пиксели, отсутствие произвольных чтений, совпадение manifest/files. Зависимости парсеров согласуй с участником 3. Подготовь RegisteredMaterial; временный серверный UUID-каталог на том же диске → проверка → import_commit guard до DB-транзакции → размещение → register_dataset → commit → ответ. Общей транзакции SQLite и файлов нет. До commit очищай лишь свои незарегистрированные файлы, после commit сохраняй все зарегистрированные исторические версии. Стартовую сверку полного реестра/остатков совместно подключает участник 3 до worker/запросов. Проверь остановки до/после размещения/commit/ответа, повреждение зарегистрированного файла и сохранность старых данных.

Не подключать медиа частично и не удалять спорные остатки/исторические материалы молча. К этому этапу текстовый импорт уже должен быть принят отдельно.

## Затем ASR/VL, данные и эффект

ASR/VL подключать через готовые ApprovedAsset/AsrGateway/VisionGateway только после проверки внутренних протоколов/адресов/авторизации/лимитов вместе с участником 3. Не предполагать OpenAI-совместимость. Текст распознавания маскировать до нормализованного хранения/текстовой модели/replay; оригинальный звук/пиксели этим не обезличиваются. Метки брать только из настоящего ответа; тайм-аут/неполное покрытие показывать ошибкой, не «потребностей нет».

Синтетические наборы/методику эффекта можно готовить независимо с участником 2: исходные данные/контур/единицы/допущения явны, компании не приписывается статистика synthetic/replay. docs/EFFECT.md существует и не пересоздаётся; формулы/измерения менять только с проверенными источниками и соответствующим обновлением документов.

## Как проверить текущую завершённую работу

Из backend самостоятельного клона со своим .venv:

    ../.venv/bin/python -m pytest -q
    ../.venv/bin/python -m scripts.check_text_pipeline
    ../.venv/bin/python -m scripts.check_text_normalizer
    ../.venv/bin/python -m scripts.check_text_import

PowerShell: ..\.venv\Scripts\python.exe -X utf8 с теми же аргументами. В этой подготовленной вложенной копии использован существующий интерпретатор той же машины ../../../../.venv/bin/python, окружение не копировалось и зависимости не обновлялись. Фактический результат: 369 passed, одно прежнее предупреждение; pipeline 202 → completed, повтор reused_steps=12, delta REPLAY_RECORD_MISSING без ложного пустого успеха. Процедуры создают новые отдельные var-каталоги, рабочую БД не используют. Windows/live/browser в этой адаптации не проверялись.
