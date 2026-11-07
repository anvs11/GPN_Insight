# Подключение обычного сервера по утверждённому профилю

После согласованного с U2 cold-start политика text_integration.version увеличена до u2-text-integration-v3. Старые v2-профили не обновляются автоматически: build_text_pipeline отклоняет их как ANALYSIS_PROFILE_INVALID. Для новой политики оператор готовит новый проверенный ProcessingProfile и точный набор с новой recording_set_version. Не удалять старые review/checkpoints и не менять опубликованные записи, чтобы заставить их совпасть. Тестовые генераторы используют отдельные var-каталоги и версии text-pipeline-authored-v2/proposal-binding-authored-v2; эти synthetic model ID/наборы не подставляются в рабочую .env.

Актуализация 2026-11-07: реальный backend/prompts/propose.md теперь включён в build_text_profile по умолчанию. Хеш и путь совпадают с промптом, который читает AnalysisService. Прежний профиль с двумя prompt hashes не дополняется автоматически: обычная сборка явно откажет с ANALYSIS_PROFILE_CONFLICT, пока оператор не подготовит новый проверенный профиль/точные replay-записи с новой recording_set_version. Не перезаписывать рабочие записи или базу. Совместимость legacy new_topic checkpoint сохраняется при прежнем точном профиле на уровне Pipeline; это не разрешение молча менять bootstrap-профиль или хеши. Проверочный новый профиль и SQLite создавались только в var. Следующее описание конфигурации 2026-11-06 является историческим, рабочая .env в этом исправлении не читалась в отчёт и не менялась.

2026-11-06. app.main:app использует существующую app.gateways.text_gateway(settings), затем read-only bootstrap, build_text_profile и build_text_pipeline. AuthoredFixtureGateway/TestComponents в обычный сервер не подключаются. Общие DTO, source hash, SQL/guard, алгоритмы U2 и правила обработки не менялись. Незавершённый HTTP/CLI-срез этой же рабочей ветки сохранён.

## Фактическая конфигурация этой сессии

В рабочем клоне .env отсутствует, режим по умолчанию replay; model_id/normalization_hash для replay и live ID/URL не настроены, активного набора по APP_RECORDINGS_ROOT нет. Утверждённый runtime-профиль не предоставлен. Пользователь выбрал «Подготовь подключение; реальные настройки позже». Поэтому рабочие .env/профиль/recordings не создавались и не переключались; обычная конфигурация остаётся неготовой. Синтетические model ID/версии ниже относятся только к изолированным проверкам, не к внутренней Qwen.

## Серверная конфигурация

Новый необязательный APP_PROCESSING_PROFILE_PATH указывает на **существующий отдельный JSON существующего ProcessingProfile**. Пути Settings считаются от корня репозитория, абсолютный доверенный серверный путь также допустим. HTTP/manifest не могут выбирать этот файл. В .env.example ключ оставлен комментарием, без выдуманного пути по умолчанию.

Для replay оператор должен задать одновременно:

    APP_MODEL_MODE=replay
    APP_PROCESSING_PROFILE_PATH=<путь к утверждённому ProcessingProfile JSON>
    APP_RECORDINGS_ROOT=<путь к проверенному точному набору>
    APP_REPLAY_MODEL_ID=<реальный model_id этих записей>
    APP_REPLAY_NORMALIZATION_HASH=<текущий NORMALIZATION_HASH>

Это шаблон, не готовые значения для запуска. Не подставлять заглушки вместо реальных значений и не копировать diagnostic-профиль в .env как производственный. model_id, recording_set_version и parameters утверждаются владельцами gateway/анализа; секреты остаются в серверных SecretStr/env и не входят в profile.parameters, replay, браузер или Git.

Профиль заранее создаётся существующим helper из **согласованных значений**, а не из первой похожей записи:

    profile = build_text_profile(
        settings,
        model_id=approved_model_id,
        recording_set_version=approved_recording_set_version,
        parameters=approved_parameters,
        max_attempts=approved_max_attempts,
    )
    with approved_profile_path.open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json())

Переменные approved_* должны быть получены от команды, здесь их значений нет. approved_parameters содержит только согласованные параметры генерации, без служебных normalization_hash/text_integration: их добавляет helper. Режим x предотвращает тихую перезапись прежнего профиля. Файл содержит только ProcessingProfile, не оболочку normalization-handoff.json/manifest и не ответы модели. Ограничение 64 КиБ, UTF-8 (BOM серверного config допускается), повторные ключи/NaN/Infinity/неверные поля отвергаются. Пользовательский manifest по-прежнему проверяет свой строгий общий валидатор.

## Последовательность создания компонентов

create_app получает Settings и создаёт configured_gateway через единственную существующую production-фабрику text_gateway(settings). Если pipeline_adapters явно не переданы, app.bootstrap.configured_text_pipeline:

1. Требует явный путь профиля. При его отсутствии ничего не выводит из metadata/контрактных примеров и не включает готовность.
2. Читает профиль ограниченно и без записи. Из его model_id/recording_set_version/параметров/бюджета попыток вызывает build_text_profile(settings, ...). Сравнивает весь результат с сохранённым профилем как canonical JSON: реальные хеши обоих промптов, normalization_version/hash, политика и лимиты, типы JSON должны совпасть. Старый профиль не исправляется молча.
3. Для replay сверяет явные Settings ID/hash с профилем. Реальный ReplayGateway проверяет checksum/schema/версии набора; все записи выбранной модели/нормализации должны иметь тот же recording_set_version, параметры, prompt_hash и prompt_version. Набор без подходящих записей не включает готовность. Записи других моделей не выбираются вместо настроенной.
4. Передаёт **тот же объект gateway** в build_text_pipeline(gateway, profile, settings). Полученные настоящие Normalizer/AnalysisService через существующий PipelineAdapters передаются RunService. app.state.text_gateway — именно этот production-gateway, без второй подстановки.

Существующий вызов create_app(settings, pipeline_adapters=adapters) сохранён для явно собранных доверенным серверным кодом компонентов. Этот аргумент не доступен из HTTP/данных. Для него по-прежнему использовать целую build_text_pipeline, а не необёрнутый AnalysisService. Обычный app.main:app не импортирует scripts/tests и не подменяет gateway при ошибке.

Bootstrap не вызывает generate/ASR/VL, не инициализирует БД, не меняет профили/recordings/источники/checkpoints. Обычный DB startup остаётся прежним. При создании app также копируются Settings: изменение исходного объекта вызывающим кодом не переименовывает работающий replay в live в health. После старта профиль зафиксирован в компонентах и снимке run; смена файла требует проверенного перезапуска/новой конфигурации, не скрытой горячей замены. Путь JSON/БД/каталога не входит в смысловой profile/request hash, поэтому перенос файлов с теми же значениями не отменяет checkpoints.

## Явная неготовность и ошибки

Без конфигурации сохраняются pipeline_ready=false и 503 INTEGRATION_NOT_READY без создания run. Если replay-ID/набор заданы, но профиля нет, health дополнительно показывает PROCESSING_PROFILE_NOT_CONFIGURED. Валидный replay-набор сам по себе не является готовностью pipeline.

При заданном пути ошибки видны кодом в существующем Health.integration_errors: PROCESSING_PROFILE_UNAVAILABLE/TOO_LARGE/INVALID/CONFLICT, REPLAY_NOT_CONFIGURED, REPLAY_PROFILE_CONFLICT, REPLAY_SET_INVALID или REPLAY_RECORD_MISSING. Полный профиль, параметры, путь, ValidationError и содержимое не возвращаются в диагностике. POST /runs остаётся 503 INTEGRATION_NOT_READY; импорт/история и обычный API не блокируются ошибкой анализа. Новых HTTP DTO не добавлено.

Внутренний live-адаптер production-фабрикой пока не реализован. При live и заданном профиле bootstrap явно возвращает MODEL_ADAPTER_NOT_IMPLEMENTED; URL/ID/JSON не объявляют модель доступной, replay не используется как замена. При появлении настоящего адаптера участник 3 должен отдельно реализовать/проверить этот путь и model health. Эта задача не проверяла доступ к Qwen и не предполагает совместимость внешнего API.

Готовность configured replay означает совместимую сборку/профиль, а не наличие записи для каждого будущего входа. Неизвестный запрос остаётся REPLAY_RECORD_MISSING в ошибках run, без live fallback и «потребностей нет». Смена active на новую recording_set_version во время процесса остаётся ANALYSIS_PROVENANCE_CONFLICT у старого зафиксированного профиля; после перезапуска тот же старый JSON даёт REPLAY_PROFILE_CONFLICT. Старые sets/версии не перезаписываются.

Материал с NormalizedDocument.errors не доходит до extraction gateway и остаётся EXTRACTION_MATERIAL_ERRORS, а не пустым успехом. Проверки exact quote/source/fragment и всей metadata из TEXT_INTEGRATION сохранены. Документ/checkpoint сохраняет pipeline, прежние нужды/ID/решения и NORMALIZATION_CONFLICT/ANALYSIS_CONFLICT не обходятся. Cold-start U2 всё ещё review, автоматического new нет.

## Выполненная проверка

На macOS/Python 3.13.0: 26 runtime-тестов и полный backend 459 passed, одно прежнее предупреждение Starlette/AnyIO. Проверены положительная автоматическая сборка обычного create_app, неправильные JSON/UTF-8/размер/дубликаты, BOM, устаревшие промпты/нормализация/лимиты/версии/JSON-типы, отсутствующие ID/набор, live неготовность, unknown replay, material/provenance ошибки и сохранность старых файлов/решений/checkpoints после повторного запуска. TestComponents/fixture gateway запрещены в проверяемом обычном сервере.

Выполнены также команды TEXT_INTEGRATION: check_text_pipeline, check_text_normalizer, check_text_import, check_import_preview, 16/7 контрактных примеров и pip check. Authored-ответы в диагностической подготовке не выдаются за результат Qwen.

scripts.check_runtime_binding сначала отдельно подготавливает явно synthetic/authored набор с реальными metadata данной диагностики. Затем запускает **обычный uvicorn app.main:app** без аргумента PipelineAdapters/кастомного app harness и без fixture-класса внутри дочернего сервера. Передаёт отдельные env/ProcessingProfile/ReplayGateway, проверяет через TCP HTTP импорт → 202/completed → источник → решение → повтор → рестарт/12 reused checkpoints → unknown delta и live 503. Рабочая .env и старые наборы не затрагиваются. Один authored seed нужен только для warm-start; это не реализованный new и не статистика компании.

Отчёт повторного HTTP-прогона после финальной проверки режима: var/runtime-binding-check-2be0e8daad5c4ad58be87ebba86b89c7/result.json. Среди фактических строк:

    configuration=isolated-synthetic; server=app.main:app; mode=replay; origins=authored; models_called=false
    binding: configured-profile=exact; gateway=ReplayGateway; fixture_injection=false
    api: inserted=4; run=202->completed; source/decision/history=preserved
    repeat/restart: unchanged=4; reused_steps=12; decision/history/checkpoints=preserved
    unknown: REPLAY_RECORD_MISSING; empty_success=false; old_source/decision/history=preserved
    live: unavailable MODEL_ADAPTER_NOT_IMPLEMENTED; replay_fallback=false
    PASS: ordinary server binding, exact replay and honest live unavailability; production config unchanged

Из backend самостоятельного клона со своим .venv:

    ../.venv/bin/python -m pytest -q tests/test_runtime_binding.py
    ../.venv/bin/python -m scripts.check_runtime_binding
    ../.venv/bin/python -m scripts.check_text_pipeline
    ../.venv/bin/python -m pytest -q

PowerShell: ..\.venv\Scripts\python.exe -X utf8 с теми же аргументами. В текущем вложенном клоне использован существующий интерпретатор этой машины ../../../../.venv/bin/python. Каждый запуск создаёт новый var-каталог, не удаляет прежние отчёты/записи. Ожидание: 26 passed, PASS процедуры и 459 passed для полного backend; UUID/время отличаются. Windows и браузер в этой задаче не запускались.

До предоставления настоящей утверждённой конфигурации рабочая установка остаётся неготовой. Подготовлен и проверен механизм подключения; производственный model_id/recording_set_version/parameters не выдумывались.

Повторное ревью перед передачей 2026-11-06: 459 backend-тестов прошли, выполнены все четыре команды текстовой интеграции и новый ordinary-server HTTP-прогон. Новый отчёт — var/runtime-binding-check-8f06b87583584166a7f9c38995df838f/result.json. Конфигурация проверки по-прежнему synthetic/authored, модели не вызывались; рабочая .env не создавалась.
