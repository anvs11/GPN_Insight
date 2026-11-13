# A4–A7: partial Pipeline, оценки, неделя и synthetic media API

Общая приёмка после merge У4 — [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md), включая проверки У1, уточнение допущения об обезличенном входе, возраст обращения и разнообразный изолированный демонабор. Повторно подключать уже готовые UI API не требуется.

Продолжение UI 09.11 по согласованию пользователя с У4: новые экраны подключены, [FRONTEND_MEDIA_WEEKLY.md](FRONTEND_MEDIA_WEEKLY.md). Backend срез ac3ae94 опубликован отдельно; замечания ниже об отсутствующих UI экранах фиксируют границу того серверного коммита. Новая UI работа — отдельная feat/frontend-media-weekly, без клона/изменения .env.

09.11.2026, `feat/weekly-partial-media`. Реализовано поверх A1–A3. Это backend-передача У1/У2/У4, не объявление новых экранов интерфейса готовыми. Профиль включается явно; прежний text профиль продолжает работать без миграции его recording hashes. Live ASR/VL/текст не подключены. Все демонстрационные ответы ниже authored, искусственная тишина/одноцветные изображения не выдаются за распознавание модели.

## Сохранение и повтор

SQLite v4 добавляет `processing_results` (immutable NormalizationResult), `processing_heads` (текущий результат/опубликованный анализ), `processing_needs` и `need_assessments`. Инициализация v1/v2/v3 сначала делает backup через SQLite backup, затем транзакционную миграцию. Повтор v4 не создаёт backup. Рабочие пользовательские базы в ходе разработки не мигрировались: проверки шли на отдельных synthetic БД в var. При следующем обычном запуске сервер сам обновит выбранную базу с backup в её соседнем `backups/`. Сбрасывать существующую SQLite не требуется.

Первый проход последовательно нормализует/извлекает по источникам; второй назначает потребности через прежний AnalysisService У2. Полный NormalizationResult хранит ошибки/покрытие. Извлечение получает только проверенный AnalysisInput.document. Успешные media checkpoints переживают прерывание и повтор; неуспех повторяется. При изменении пригодного контекста меняется analysis_digest, поэтому прежний extraction не считается полным. Новое извлечение, активация needs, retirement старых links и запись checkpoint атомарны; файлы не входят в SQLite-транзакцию.

ID потребности зависит от точной версии/содержимого источника и содержимого самой потребности, не её места в ответе. Неизменная потребность сохраняет ID/назначение/ручной override. Изменённая/исчезнувшая становится неактивной, её фрагмент/история остаются. Topic ID, owner/status, паспорт и DecisionEvent не сбрасываются. При изменении активных свидетельств topic.version увеличивается; устаревшая запись решения получает 409. Ручная связь с исчезнувшей потребностью получает requires_recheck, источник — manual_recheck_required, прежняя тема — pending_source_ids. Новые потребности при таких overrides попадают в review, не переносят ручное решение молча.

Нормализация может быть опубликована до извлечения. Если новый extraction ещё не завершён, source.analysis_pending=true и тема показывает pending_source_ids; прежний анализ виден как ожидающий обновления. Если неудачны все материалы, пустой extraction успех не создаётся. Empty после успешной нормализации и failed различаются. Partial run завершается completed_with_errors, даже если тема из успешной части уже сохранена.

## Явная конфигурация

`build_partial_profile(...)` включает media recipe/model/parameters/set и classifier prompt hash. `app_processing_profile_path` указывает этот утверждённый JSON; `APP_MEDIA_RECORDINGS_PATH` — точный immutable media JSON; `APP_MEDIA_CACHE_ROOT` — каталог успешных результатов. Text `APP_REPLAY_NORMALIZATION_HASH` должен совпадать с normalization_hash нового профиля. Все параметры partial включены в точные text request keys. Старые файлы профилей/наборов не дописываются и не переименовываются.

`configured_text_pipeline` выбирает partial только по явному `parameters.partial_media`. Отсутствующий/чужой профиль/набор не делает Pipeline готовым. Неизвестный request даёт REPLAY_RECORD_MISSING; нет сети, похожей записи или live fallback. Health помечает ASR/VL как MEDIA_REPLAY_READY только после проверки известных записей, с пояснением «только точные известные входы, live не подключён». Это доступность replay, не живой модели.

Пример самостоятельной подготовки окружения есть в `scripts.partial_pipeline_fixture.prepare_pipeline_demo`; это исключительно test/dev-процедура. Runtime не импортирует scripts и не вставляет synthetic карточки в пустую базу. `.env.example` содержит только безопасные настройки; рабочий `.env` не перезаписывался.

## Контракт У2: категория и приоритет

`assessment_schemas.NeedAssessment`: category, priority, reason, uncertainties, evidence_refs. Категории: suspected_defect, usability_confusion, improvement_request, operational_request, unclassified. Приоритеты: normal, elevated, potentially_critical, unknown. Unknown и potentially_critical требуют непустого списка неизвестностей; основание всегда содержит точные цитаты пригодных фрагментов и связывается с need. Приоритет не заменяет Topic.status/need_type и не утверждает доказанный ущерб/массовость. Слова/эмоциональность/частота не используются сервером как классификатор.

Операция `classify_need`, промпт `backend/prompts/classify.md`, версия `classify-need-v1` вызываются после extraction и до assignment. Вход — need + пригодный document + analysis_digest. Отдельный `AssessmentResult` хранит need_id/digest/ответ и RecordingMeta. Model ID, normalization/prompt/schema/request hashes, parameters, set version и origin проверяются. Смена промпта меняет профиль/точный ключ. Неизвестная/невалидная запись оставляет явную classification ошибку и неоценённую потребность, а не normal. Распознаваемые немаскированные данные в reason/uncertainties отвергаются.

Существующие extract/assign/propose промпты и алгоритмы У2 не переписывались. До независимой разметки/реального доступа качество классификации не измерено. У2 получает этот интерфейс и synthetic authored пример для продолжения своих evaluation/recordings. Согласование словаря/критичности подтверждено пользователем от У2; имя ответственного в сообщении не указано.

## HTTP для У4

Все пути ниже относительно `/api/v1`, старые пути/DTO сохраняются. Полные схемы доступны локально в `/openapi.json`; CLI export-schemas экспортирует 54 схемы. Валидные synthetic примеры — `data/contracts/server-completion.json`.

| Метод/путь | Ответ/смысл |
|---|---|
| GET /sources/{source_id}/processing | ProcessingDetail partial-source-v1: полный masked result, current_head_revision, is_current, analysis_pending, manual_recheck_required, assessments и unassessed_need_ids |
| GET /sources/{source_id}/processing?source_version=1&revision_id={sha} | Точная историческая ревизия той же версии источника; её assessments, is_current=false. analysis_pending=null означает, что это не текущий head, а не готовый новый анализ |
| GET /topics/{topic_id}/assessment-summary | topic-assessment-summary-v1, topic_version, unit=sources, source_count, unassessed_source_count, категории/приоритеты по distinct source_id |
| GET /reports/weekly/period | CompletedWeek: даты понедельник–воскресенье MSK, UTC start и exclusive end |
| GET /reports/weekly | Markdown UTF-8, скачивание gpn-insight-weekly.md |
| GET/HEAD /sources/{source_id}/versions/{source_version}/assets/{asset_id}?product_id=webneft | Только synthetic зарегистрированный файл, известный MIME, no-store/nosniff и нейтральное имя. Нет параметра path/URL |

SourceDetail получил optional для совместимости frontend поля current_result_revision, processing_state, analysis_pending, manual_recheck_required. Run получил processing_policy=partial-media-v1, material_coverage (unit=materials) и source_coverage (unit=sources). Для нового профиля прежние *_materials считают реальные outcomes: непустой исходный текст (или пустой text-only источник) плюс каждое вложение. Новые coverage snapshots содержат обработанную к данному моменту часть, не прогноз оставшейся. Для legacy профиля новые поля null; старые checkpoints/DTO не переопределяются.

В category/priority summary один source может входить в несколько строк, но в каждой считается один раз. Сумма категорий/приоритетов не равна общему числу обращений. Missing assessment и priority=unknown — разные состояния; старые данные без оценки не становятся normal. Отчёт показывает обе группы, потенциально важные сигналы с основаниями/ограничениями, errors и coverage. Темы с несколькими needs не удваивают общий источник.

Файловый ответ: GET=200, одно допустимое аудио `Range: bytes=0-15`/`bytes=16-`/`bytes=-16`=206 с Content-Range; некорректный/multipart/неудовлетворимый range=416, Content-Range=bytes */size. Для image range не поддержан (416). HEAD возвращает только заголовки. Без подходящего If-Range validator отдаётся целый файл. Несинтетический оригинал=403; чужой asset/version=404; повреждённый/недоступный registered файл=503. Клиент не передаёт локальный путь; сервер дважды безопасно читает/сверяет bytes через resolver и checked_open, не перенаправляет на source_url.

Frontend `api.ts` экспортирует `sourceMediaUrl(sourceId, sourceVersion, assetId)` и `weeklyReportUrl`, новые read поля типизированы/проверяются. Новые кнопки/плеер/форма загрузки media этим backend срезом не добавлены: это продолжение У4. Nonsynthetic originals остаются закрыты; это не corporate authentication и не гарантия полного обезличивания производных текстов.

## Неделя и отчёт

Последняя завершённая неделя рассчитывается по clock приложения, Europe/Moscow. Например, 09.11.2026 в понедельник: 02.11–08.11 включительно, UTC `[2026-11-01T21:00:00Z, 2026-11-08T21:00:00Z)`. В воскресенье до полуночи Москвы текущая неделя ещё не считается завершённой. Переходы месяца/года/високосного февраля проверены с фиксированным clock. Публичного параметра подмены текущего времени нет.

Отбор идёт по created_at источника, не времени анализа/решения. Period и all-time подписаны отдельно. Динамика сравнивает distinct зарегистрированные source_id завершённой и предыдущей недели; это не incident count и не доказательство эффекта внедрения. Все данные сводки/динамики читаются в одном SQLite snapshot. Review не блокирует формирование.

Готово формирование по запросу HTTP/CLI. Запланированная автоматическая генерация в понедельник, постоянно работающий сервер и внешняя доставка не реализованы/не поручены. Не обещать рассылку по одному endpoint.

## Команды проверки

Из корня PowerShell:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_completion_contracts
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_partial_weekly_integration
..\.venv\Scripts\python.exe -X utf8 -m pytest -q -rs
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -X utf8 -m app.cli export-schemas
..\.venv\Scripts\python.exe -m pip check
```

На Linux/macOS использовать ../.venv/bin/python. Ручная процедура сама создаёт отдельную synthetic БД, artificial файлы и authored recordings в новом var/partial-weekly-check-UUID; запускает/останавливает свои localhost процессы, не требует Node и не меняет рабочие файлы. Первый процесс имеет явно тестовый барьер для воспроизводимого прерывания после нормализации, затем используется обычный app.main:app без тестового adapter. Она проверяет активный повторный клик, interrupted/reuse, partial 4/5→5/5, решение/passport/history/409, старую ревизию, binary/ranges, weekly и restart/repeat. Успех подтверждают строки PASS и result.json. Нет live/network моделей. Последний отчёт содержит http-examples.json и weekly.md для команды.

Недельная сводка из уже подготовленной выбранной БД (из backend, обычные APP_DB_PATH/настройки окружения):

```powershell
..\.venv\Scripts\python.exe -X utf8 -m app.cli report --completed-week --output ..\var\weekly-review.md
```

Если файл уже существует, выбрать новое имя: CLI не перезаписывает его. Для произвольного периода прежние --date-from/--date-to; они несовместимы с --completed-week.

Для работающего локального сервера У4 может проверить (в отдельном терминале, из корня):

```powershell
$baseUrl = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$baseUrl/reports/weekly/period"
Invoke-WebRequest "$baseUrl/reports/weekly" -OutFile var/weekly-from-http.md
$cards = @(Invoke-RestMethod "$baseUrl/topics?support_level=all")
$sourceId = $cards[0].sources[0].ticket.source_id
$source = Invoke-RestMethod "$baseUrl/sources/$sourceId"
$processing = Invoke-RestMethod "$baseUrl/sources/$sourceId/processing"
$processing.result.coverage
Invoke-RestMethod "$baseUrl/topics/$($cards[0].topic.topic_id)/assessment-summary"
$assetId = $source.ticket.assets[0].asset_id
$assetUrl = "$baseUrl/sources/$sourceId/versions/$($source.source_version)/assets/$assetId"
Invoke-WebRequest $assetUrl -Method Head
```

Эти последние команды предполагают уже загруженный/обработанный synthetic media источник; пустая production БД не содержит автоматически карточек. Для автоматической подготовки/проверки использовать check_partial_weekly_integration выше. После обычной text фикстуры /processing может законно вернуть 404 PROCESSING_RESULT_NOT_FOUND, а assets может быть пустым. Факт наличия статического примера не обещает подходящую запись replay для произвольного импорта.

## Фактическая приёмка и ограничения

Финально выполнено: 743 backend passed/4 Windows symlink skips/1 прежний Starlette warning (57.47s), 260 frontend tests и build, четыре новых и прежние contract examples, export54/pip check. Новый HTTP/interrupt/restart: var/partial-weekly-check-a8ae76647054478fa176aa4e3ed2ee31/result.json; отдельный CLI weekly-cli.md сохранён там же. Сохранность пяти private файлов (.env и рабочих SQLite), семи legacy model schemas, импорта/hash/прежних промптов/pins/UI components проверена: var/reviews/partial-weekly-integration-20261109/result.json. Commit/push не выполнялись.

Результаты записываются в `docs/PARTIAL_WEEKLY_HANDOFF.md` и общем ExecPlan. HTTP/restart выполнены настоящими локальными процессами; визуальный browser-click прогон остановлен Computer Use, который не смог уверенно определить URL Windows браузера. Обход не выполнялся. Полную визуальную приёмку новых экранов нельзя заменить HTTP или unit-тестами; она остаётся при подключении У4.
