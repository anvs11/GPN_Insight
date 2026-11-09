# Partial/media foundation: выполненные A1–A3

Этот документ фиксирует завершённый срез A1–A3. Его следующий A4–A7 уже реализован: [PARTIAL_WEEKLY_HANDOFF.md](PARTIAL_WEEKLY_HANDOFF.md). Указанные ниже ограничения production Pipeline/SQL publication описывают прежнее состояние, а не нынешний partial профиль. Legacy text/live ограничения и результаты предыдущей приёмки сохранены.

09.11.2026, feature `feat/weekly-partial-media` поверх main `620b741`. Этот срез реализует контракты частичной нормализации, safety fixtures/выдачу originals и отдельный media Normalizer с точным ASR/VL replay. **Обычный production Pipeline ещё не подключает media**: A4 требует публикации revisions в SQLite и корректного обновления needs/связей/прогресса. Live не подключён. Старые text schemas/hashes/prompts/import/SQL/checkpoints не переписывались.

## Контракты A1

Общие новые типы находятся в `backend/app/processing_schemas.py`, записи media — в `media_replay.py`. `NormalizationResult` связывает source/product/version/content_hash/synthetic/data_origin, profile_hash, полный NormalizedDocument, исход каждого материала, MaterialCoverage, state, revision_id и analysis_digest. Source data_origin=imported|authored независим от metadata.origin=model|authored; импортированная синтетика не превращается в ответ модели. Ошибка принадлежит своему source/asset, failed outcome не содержит успешных фрагментов/metadata, пустой успешный ответ отличается от ошибки. Document хранит ровно union фрагментов и ошибок outcomes. `model_validate` проверяет hashes/revision и счётчики, а не принимает их как числа от модели.

MaterialCoverage.unit=`materials`: total/successful/failed/empty/usable/ratio. Текст считается одной единицей при наличии текста либо у text-only пустого обращения; каждое вложение — отдельной единицей. successful включает empty; usable означает непустой пригодный текст. State: complete (есть context, без ошибок), partial (часть материалов успешна, часть нет), failed (нет успешного материала), empty (все обработаны, но пригодного текста нет). Никакой state не является оценкой качества модели.

SourceCoverage.unit=`sources` считает complete/partial/failed/empty источники отдельно. Legacy Run по-прежнему считает источники в полях с историческим именем `*_materials`; смысл этих полей не менялся. Агрегация новых counts в production progress — A4, не выполненная функция.

`revision_id` — semantic SHA полного immutable результата, а не возрастающий номер SQLite/source_version. Повтор идентичного результата даёт тот же ID, добавленный контекст/изменённая ошибка — другой. `analysis_digest` связывает профиль и только пригодные успешные фрагменты; изменение ошибки без изменения context не требует нового модельного extraction. При появлении пригодного фрагмента digest меняется. `analysis_input(result)` повторно валидирует envelope и возвращает AnalysisInput с error-free model-facing document и **явными source_errors/coverage/revision/profile/digest снаружи**; без usable context возвращает None. Это не удаление ошибок из сохранённого документа.

Политика partial утверждена пользователем. Валидация/проекция готовы, но хранение ревизий/supersession/пересчёт связей в SQLite ещё нет. Нельзя передавать новую проекцию в legacy Pipeline, просто отключив MaterialError guard: его старые extraction keys и immutable document/needs не умеют безопасно дополнить контекст. A4 использует новые revision/digest с сохранением решений/паспортов/overrides.

## Safety и оригиналы A2

`data/security/synthetic-safety.json` содержит PII_SAMPLE, UPLOAD_BAD, INJ_TEXT, ожидаемые маски и ограничения. Проверки используют реальные importer/masking/строгие text recordings/NeedExtractor и HTTP source/card/list/decision/passport/report. Указания в обращении сохраняются как данные, проверка фрагментов/цитат не отключается, чтение файла по инструкции не выполняется. Authored-ответ в regression не является доказательством устойчивости живой модели. Имена/адреса/все форматы ПДн нынешним mask_pii не гарантируются.

Единый `public_views.public_source/public_topic` применяется ко всем HTTP TopicDetail/SourceDetail ответам, включая source/target merge-preview. Добавлено read-поле `SourceDetail.original_access=available|restricted`, default available у внутренних legacy объектов; TypeScript принимает старые ответы без поля и проверяет новые значения. Для nonsynthetic API возвращает `restricted`, пустой ticket.text, requester_id=null, нейтральное имя файла `restricted.<ext>`, source_url=null. Идентификаторы/даты/нормализованный документ/needs/программная статистика сохраняются. Synthetic оригинал остаётся доступным. Raw repository/SQLite и pipeline inputs не изменяются, повторное открытие подтверждает сохранность. Public projection не является исходным manifest для повторного импорта.

Это **ограничение originals, а не корпоративная авторизация или обещание обезличить весь производный контент**. Подготовленные фрагменты/цитаты/ручные комментарии могут сохранять имена/адреса, которые маскирование не распознаёт. Пока нет полной разрешённой access policy, реальные данные не подключать. CLI/repository — доверенные локальные инструменты, не публичный API; бинарного API в этом срезе нет. Открыть original query-параметром нельзя.

## Media replay и Normalizer A3

`build_media_profile` фиксирует фактический recipe/masking hash, mode, явно заданные component model_id/parameters/prompt+version и recording_set_version. Пример использует имена `synthetic-asr-no-live-model`/`synthetic-vl-no-live-model`, не реальные ASR endpoints. Изменение recipe/операции/схемы/bytes/параметров/модели/набора проверяется точно; TextRequest/старые request_key не менялись.

MediaRequest содержит asset_id/content_hash/byte_size/MIME, transcribe|describe и точные model/recipe/set. Absolute_path и URL в ключ/record не попадают. MediaRecording связывает SHA схемы MediaText, request hash, prompt hash при наличии, параметры, normalization_hash/model_id/origin/set. Ответ маскируется **до** публикации; проверка отвергает немаскированную запись, чужой asset/kind, bool/string timestamps, дубль fragment ID и выход за реальные audio bounds. Временные метки не создаются, если их нет. JSON строго UTF-8, без повторных ключей/NaN, с лимитами.

`publish_media_recordings(path, records, profile)` публикует explicit immutable JSON set со checksums: новый файл EXCL после записи полных bytes, точный повтор допустим, другое содержимое по тому же пути запрещено. Нет active-pointer, сканирования похожих наборов или сети; новая версия задаётся явно с новым путём/профилем. У файлового namespace нет глобального реестра всех наборов на машине; не переиспользовать semantic set version с иными ответами в другом месте. Повреждённый/чужой набор — MEDIA_REPLAY_SET_INVALID/PROFILE_CONFLICT, неизвестный запрос — REPLAY_RECORD_MISSING. Origin model в replay означает происхождение записи, не текущий вызов модели; authored виден явно. Mode live отвергается с MEDIA_LIVE_NOT_CONNECTED.

`MediaNormalizer(repository, settings, profile, asr_gateway=…, vision_gateway=…, cache_root=…)` возвращает NormalizationResult, не legacy NormalizedDocument. Он проверяет точный зарегистрированный источник/hash/материалы, получает persistent ApprovedAsset через resolver, безопасно перечитывает bytes и duration. Текст обрабатывается локально, вложения независимо; пригодные masked фрагменты сохраняются при ошибке другого вложения. Media replay пилота допускает только synthetic материалы. Искусственная тишина/одноцветные изображения и authored-тексты — contract fixtures, не реальные результаты распознавания.

Независимые success/empty checkpoints связывают source/version/asset/hash/profile/request, masked outcomes и checksum. Cache не хранит failures. Перед cache-hit снова проверяются file integrity и metadata; повреждение cache даёт MEDIA_CHECKPOINT_INVALID без удаления/тихого обхода. Перезапуск Normalizer/новое соединение переиспользует успехи, поздний успех материала дополняет context. Fragment ID зависят от source/version/content/asset/profile/segment и не сдвигаются из-за неудачи другого asset. Результаты пока возвращаются отдельно и не публикуются поверх legacy SQLite documents.

## Проверка из PowerShell

Из корня проекта:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_processing_contracts
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_partial_media_foundation
..\.venv\Scripts\python.exe -X utf8 -m pytest -q -rs
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -X utf8 -m app.cli export-schemas
..\.venv\Scripts\python.exe -m pip check
```

На Linux/macOS использовать ../.venv/bin/python. Первый скрипт проверяет 8 shared JSON-примеров из `data/contracts/partial-processing.json` без БД/моделей. Экспорт CLI теперь содержит 49 schemas (38 прежних + 11 новых), legacy model-facing schemas не изменились. Вторая команда сама создаёт новую synthetic БД и четыре формата/recordings в var, сохраняет authored тему/решение и проверяет partial/retry/reopen без публикации новой normalisation в SQL.

Ожидаемые строки:

```text
partial: success=4/5; failed=1; usable context available; original errors retained
retry/reopen: success=5/5; gateway calls=3+1; stable fragments; new revision/digest; old snapshot valid
PASS: SQLite/decision/history unchanged; private files preserved; production media pipeline not connected
```

Материалов пять: исходный текст + WAV/MP3/PNG/JPEG. Четыре вызова — чтение authored записей, не моделей. Отчёт/partial/full snapshots остаются в отдельном `var/partial-media-check-…`. Рабочие .env/SQLite не участвуют. Legacy scripts/check_server_completion остаётся отдельной регрессией text API/сценарного replay. Фактические результаты текущего среза — в ExecPlan; ожидаемый вывод не является доказательством запуска.

## Передача

У2: новые DTO/projection/digest, прежние NormalizedDocument/ExtractedNeed/prompts не менялись. Нужна A4-сборка, которая передаст только success view, но сохранит errors/coverage отдельно. Категории/приоритеты и classifier prompts остаются A5.

У4: additive original_access уже совместим с текущим source viewer (nonsynthetic original скрывается и сервером). Media/progress/revision UI и binary endpoint пока не готовы; новые единицы counts не подменяют legacy Run fields.

У1/У3: resolver, partial Normalizer, strict recordings и success-cache готовы как отдельная проверяемая связка. Следующий шаг A4 — SQL revision publication, корректные потребности/links при дополнении контекста и штатный bootstrap; затем binary API/недельный отчёт. Не объявлять production media/live готовыми после одного успешного fixture replay.
