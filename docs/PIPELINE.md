# Запуски, checkpoints и строгий текстовый replay

На этом этапе участник 3 реализовал инфраструктуру запуска. `RunService` использует один `ThreadPoolExecutor(max_workers=1)`. API читает состояния из SQLite, а pipeline вызывает переданные нормализатор и анализ. **Нормализатор участника 1, извлечение/группировка участника 2 и настоящий live TextGateway ещё не подключены.** Обычный сервер не подставляет тестовую реализацию: POST возвращает `503 INTEGRATION_NOT_READY`, run не создаётся. Синтетические компоненты находятся только в `backend/tests/pipeline_adapters.py`.

Подтянутый U1-01 проверен отдельно: строгий текстовый preview, общий source_content_hash и чтение counts без записи продолжают работать. Фиксацию импорта и нормализацию за участника 1 этот этап не добавляет.

## HTTP-контракт для участника 4

`POST /api/v1/runs` принимает [run.json](../data/contracts/run.json): `product_id=webneft`, необязательные `date_from`/`date_to` (даты, обе границы включены, календарь Europe/Moscow). Начало позже окончания — 422. `mode` и run_id клиент не задаёт: режим берётся из конфигурации сервера. Ответ при подключённых компонентах — **202 с объектом Run**, содержащим run_id; повтор во время queued/running возвращает тот же активный объект/ID. Новый запрос после завершения создаёт новый run и переиспользует успешные checkpoints.

`GET /api/v1/runs/{run_id}` возвращает сохранённый Run, неизвестный ID — 404 RUN_NOT_FOUND. Состояния: queued → running → completed/completed_with_errors/failed, либо interrupted. После аварийного выхода старый queued/running при следующем старте становится interrupted, автоматически продолжать его под прежним ID сервер не будет. Повторный POST создаёт новый ID с переиспользованием успешных этапов.

При остановке worker запрещает последующие commit и помечает активный run interrupted. Уже начатый вызов компонента нельзя безопасно убить внутри Python-потока: его завершение ограничивают тайм-ауты реального gateway. Полноценное принудительное прерывание модельного вызова в этом этапе не заявляется.

Run содержит прежние счётчики материалов/coverage/errors/timestamps, а также `origins`, `completed_steps`, `reused_steps`. Для общей ошибки worker есть failure_code/failure_message, без выдуманного source_id. Для текущего текстового среза один источник — один текстовый материал. `successful_materials`/coverage относятся к нормализации материала, а ошибки извлечения/назначения отдельно видны в errors и состоянии completed_with_errors. Даже при coverage=1 анализ может иметь ошибки; интерфейс обязан показывать state/errors/failure_code. Источник с медиа получает явную MEDIA_PIPELINE_NOT_IMPLEMENTED, его текст и вложения не выдаются за обработанные. Полная мультимодальная оркестрация ещё не подключена.

Health теперь показывает `processing_state=idle|busy`, `pipeline_ready` и `integration_errors`. Idle не означает готовность моделей. Пока компонентов нет, pipeline_ready=false и перечислены NORMALIZER_NOT_CONNECTED/ANALYSIS_NOT_CONNECTED. Для текста реализован строгий replay: без конфигурации REPLAY_NOT_CONFIGURED, с проверенными совпадающими записями REPLAY_READY. ASR/VL replay пока REPLAY_NOT_IMPLEMENTED. Режим и origins всегда показываются рядом с результатом. Код REPLAY_READY относится к доступным сохранённым запросам, не к живой модели.

## Граница с участниками 1 и 2

Фабрика `create_app(settings, pipeline_adapters=PipelineAdapters(...))` принимает серверные компоненты. Клиентский JSON не выбирает классы и модули. Подключение владельцы делают через эту фабрику после согласования своих реализаций; тестовый адаптер нельзя импортировать в обычный main.py или выбирать через .env.

Участнику 1 предоставлен `Normalizer` из app.pipeline:

    normalize_ticket(ticket: TicketInput, *, content_hash: str, source_version: int) -> NormalizedDocument

Возвращаются замаскированные нормализованные фрагменты и явные ошибки. product/source/version/content_hash должны совпасть с сохранённым источником, normalization_version — с профилем. Pipeline присваивает fragment_id детерминированно по product/source/version/content_hash/индексу, до вызова извлечения. Загрузку файлов, маскирование и ASR/VL по-прежнему реализует участник 1. Этот интерфейс передаёт контекст уже зарегистрированного источника и не меняет оболочку manifest, DatasetRegistration или алгоритм source_content_hash.

Участнику 2 предоставлен `Analysis`:

    extract_needs(document: NormalizedDocument) -> ExtractionResult
    assign_topic(need: ExtractedNeed, topic_passports: CatalogSnapshot) -> AssignmentResult

ExtractionResult содержит needs и metadata: RecordingMeta[] фактически использованных ответов. Pipeline назначает need_id по product/source/version/content_hash/индексу ответа. AssignmentResult содержит серверный TopicAssignment, new_topic только для new, metadata. Это итог сервиса анализа, **не схема ответа модели**. Модельные ответы по порциям остаются BatchAssignment. Полный поиск, подбор порций, промпты и формирование паспорта реализует участник 2; pipeline их не имитирует.

При new паспорт/тема передаются участником 2, а topic_id сервер выводит из первой need_id через stable_id('topic', need_id). В этом последовательном интерфейсе новая тема публикуется для одной текущей потребности, последующие назначения видят её в следующем снимке. При match сервер добавляет связь без переписывания заголовка/паспорта/решения. При review сохраняется открытый ReviewItem; политику его разрешения этот этап не реализует. Проверяются версия каталога, ID/версии паспортов и ссылка/цитата текущей потребности. Изменение каталога до commit даёт CATALOG_CHANGED с возможностью повтора в новом run.

`ProcessingProfile` фиксирует model_id, normalization_version, хеши **содержимого** промптов extract_needs/assign_topic, параметры и неизменяемую recording_set_version. Metadata каждого этапа должны совпасть с моделью, промптом и версией набора. В live допустим только origin=model; authored не подменяет модельный ответ. В replay допустимы проверенные model/authored записи, origins видны в Run. Профиль обязан включать все значимые настройки анализа в parameters; модельные тайм-ауты/ограниченные повторы остаются обязанностью реальных gateway-адаптеров. У инфраструктуры нет неограниченного повторения ошибочного запроса.

Сначала нормализуются и извлекаются потребности всех выбранных источников, затем выполняются назначения в порядке `(created_at как момент времени, source_id, индекс потребности)`. Шаги не вызываются параллельно. Нормализация, извлечение и каждое назначение сохраняются отдельно; данные шага и успешный checkpoint — одна транзакция. Ошибочный этап не получает успешного checkpoint и может повториться в следующем run. Старые ручные переопределения и решения не отменяются. Checkpoint опубликованного назначения сохраняет факт его выполнения, а не автоматически пересчитывается после ручной правки.

Новая версия источника после ручного переопределения направляется в отдельный review: прежний requires_recheck не разрешает автоматически восстановить исключённую связь. Review и checkpoint сохраняются атомарно, повтор не создаёт второй review; решение о новой версии принимает человек через будущий сервис участника 2.

Произвольная смена профиля и повторная обработка уже опубликованного анализа пока могут дать NORMALIZATION_CONFLICT/ANALYSIS_CONFLICT; автоматической перезаписи старых результатов нет. Старый seed-demo не является входом нового анализа с известным профилем. Для первой проверки pipeline используйте отдельную процедуру ниже, не запускайте переобработку рабочей демо-базы ради эксперимента.

## SQLite v3 и общая защита импорта

Миграция добавляет только `run_inputs` и `stage_checkpoints`. Initialize перед v1/v2 делает SQLite backup, затем атомарно выполняет нужные миграции до v3. Повтор на v3 безопасен. Прежние источники/материалы/темы/решения/история не сбрасываются. U1-01 preview читает и v2, и v3 без создания/миграции.

Run_inputs хранит снимок выбранных источников и профиль. Ключ checkpoint содержит stage/product/source/version/content_hash/need_id, профиль и mode: успешный live-этап не используется вместо replay и наоборот. Run и его входы создаются одной транзакцией; ошибка снимка откатывает queued.

`Repository.import_commit()` — общий guard для одного серверного процесса. Будущая HTTP/CLI-фиксация участника 1 использует:

    with repo.import_commit():
        # Только после dry_run, в рамках его собственного протокола восстановления:
        # перемещение проверенных файлов, затем:
        result = repo.register_dataset(batch)

Начинать guard нужно **до** открытия внешней SQLite-транзакции и размещения файлов. Register_dataset сам использует тот же guard, вложенный вызов в одном потоке разрешён. Конкурентные обычные регистрации сериализуются; нельзя ждать другого импорта, уже удерживая свою DB-транзакцию — такой вызов получает IMPORT_ACTIVE. Во время queued/running импорт получает RUN_ACTIVE; во время guard фиксации старт run получает IMPORT_ACTIVE. Проверка и создание queued защищены также между разными экземплярами Repository/RunService с одним путём БД. Блокировка не удерживается во время модели и не заменяет протокол восстановления файлов. Общей SQLite-транзакции с файловой системой нет. Несколько серверных процессов не поддерживаются — запускайте `--workers 1`.

## Точный текстовый replay и каркас record

`app.replay.request_key()` вычисляет SHA-256 canonical JSON полного TextRequest, схемы ответа, model_id и normalization_hash. Ключи JSON-объектов сортируются, порядок массивов сохраняется; prompt входит целиком вместе с prompt_version. В envelope не добавляются транспортный mode, run_id, часы и временные пути. Модель/нормализация не выводятся из похожей записи. Participant 2 должен передавать только смысловой вход, включая фактическую упорядоченную порцию паспортов с ID/версиями; случайные служебные поля в request.data недопустимы.

Набор содержит manifest с SHA-256 каждого артефакта и отдельные файлы по request_hash. Проверяются схема, хеш запроса/промпта/артефакта, параметры, версия набора и origin. Неизвестный запрос — REPLAY_RECORD_MISSING, несовпадение схемы — явная ошибка, повреждённый набор — REPLAY_SET_INVALID. Сеть и скрытое переключение в live отсутствуют. Эти проверки целостности не являются криптографическим доказательством происхождения модели или оценкой качества её ответа.

`RecordingGateway(inner: TextGateway, ...)` — каркас захвата реальных текстовых вызовов. Authored-ответ он не принимает как model, сохраняет запрос/ответ и metadata в памяти. Владелец сценария вызывает publish(root, scenario_succeeded=True) только после успешного завершения всего сценария; без явного подтверждения публикация отклоняется. Любая ошибка захвата делает сессию непубликуемой, даже если раньше были успешные вызовы. Запись строится в новом каталоге; после полной проверки атомарно переключается active.json, прежний указатель имеет backup. Содержимое опубликованной версии неизменно: для изменённого набора нужна новая версия. Рабочая SQLite-база при record/verify не используется.

CLI `record --authored-input` — **только ручной синтетический образец**, принудительно origin=authored. Это не вызов Qwen. `record` без этого флага отказывает с LIVE_RECORDING_NOT_CONNECTED. `verify-recordings` проверяет артефакты и при --requests точные входы, без сети; это начальная структурная проверка текста, не полный end-to-end сценарий record/evaluate/holdout. Полный модельный record и сценарии качества ждут реализации участников 1/2.

Проверка перед передачей дополнительно закрепила неизменность всех ранее опубликованных версий, включая историю backup-указателей: цепочка v1 → v2 не разрешает повторно выпустить другое содержимое под v1. Публикации одного каталога сериализуются через эксклюзивный .publish.lock. При прерванном record оставшаяся блокировка даёт RECORDING_PUBLISH_ACTIVE; автоматического захвата чужого процесса или удаления файлов нет, для новой проверки можно выбрать новый каталог. До переключения active.json перечитанные артефакты сверяются также с checksum manifest. RecordingGateway отклоняет несовпадение request/prompt/normalization hashes и параметров реального адаптера, а не переписывает эти metadata под запрос.

На пользовательском Windows-терминале второе предупреждение pytest оказалось PytestCacheWarning/WinError 5 при записи backend/.pytest_cache. Оно касается кэша node IDs, не SQLite и не результата обычного полного прогона. Права системы и старый кэш не менялись. При необходимости отдельного доступного кэша можно выполнить из backend/:

    $testCache = '../var/pytest-cache-' + [guid]::NewGuid().ToString('N')
    ..\.venv\Scripts\python.exe -X utf8 -m pytest -q -o "cache_dir=$testCache"

Это переносит диагностический кэш в новый игнорируемый каталог, не скрывая предупреждения или ошибки тестов.

## Проверка из PowerShell

Из корня репозитория; на macOS вместо указанного Windows Python используйте `../.venv/bin/python` из backend/.

```powershell
Set-Location C:\Users\user\projects\GPN_Insight\backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_import_preview
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_pipeline
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -m pip check
```

Check_pipeline явно печатает `adapters=tests-only`, создаёт отдельную var/pipeline-check-*/check.db и собственный localhost-сервер на свободном порту. Он проверяет 202/повтор клика, прерывает только свой процесс между проходами, подтверждает interrupted и повтор четырёх этапов без новых вызовов, затем сохранение решения и повтор всех шести шагов без дублей. После проверки его сервер остановлен. Это не команда запуска production-анализа.

Для строгого authored replay (из backend/):

```powershell
$records = '../var/recordings-check-' + [guid]::NewGuid().ToString('N')
..\.venv\Scripts\python.exe -X utf8 -m app.cli record `
  --authored-input ../data/recordings/examples/text-authored.json --recordings $records
..\.venv\Scripts\python.exe -X utf8 -m app.cli verify-recordings `
  --recordings $records --requests ../data/recordings/examples/text-authored.json
..\.venv\Scripts\python.exe -X utf8 -m app.cli verify-recordings `
  --recordings $records --requests ../data/recordings/examples/text-unknown.json
$LASTEXITCODE
```

Ожидается: record_count=1/origins=[authored]; проверка известного входа — network_used=false/verified_requests=1. Неизвестный вход даёт REPLAY_RECORD_MISSING и `$LASTEXITCODE=1`; это успешная проверка отказа, не неисправность. Ни demo, ни holdout команды не меняют. Для health текстового replay задайте `$env:APP_RECORDINGS_ROOT = (Resolve-Path -LiteralPath $records).Path`, APP_REPLAY_MODEL_ID=synthetic-contract-no-live-model и APP_REPLAY_NORMALIZATION_HASH равным 64 буквам `a`. CLI-путь считается от текущей папки, а относительные настройки — от корня репозитория, поэтому здесь нужен абсолютный путь. Pipeline от этого не станет готовым: компонентов нормализации/анализа всё ещё нет.

Фактически на Windows/Python 3.13.0 проверены 176 тестов, команды CLI, исходный preview и scripts.check_pipeline с настоящим Uvicorn. Новая версия pipeline на macOS самостоятельно не запускалась; эквивалентные команды не считаются отчётом о таком прогоне.
