# U1 → U2/U3: медиа-источники, метаданные и provenance

Актуальный A4–A7: partial/media bootstrap, SQLite revisions, classify_need и binary/weekly HTTP подключены. Передача У2/У4: [PARTIAL_WEEKLY_HANDOFF.md](PARTIAL_WEEKLY_HANDOFF.md). Старые text extraction DTO/prompts и импорт сохранены; только явно настроенный synthetic/authored replay, live не подключён. Ранние ограничения ниже относятся к прежним этапам/legacy профилю.

Реализация A1–A3 09.11: готовы processing_schemas, отдельный MediaNormalizer, точный ASR/VL replay/profile и независимые success checkpoints; 8 shared примеров в data/contracts/partial-processing.json. [Актуальный интерфейс и ограничения](PARTIAL_MEDIA_FOUNDATION.md). Legacy CheckedAnalysis/Pipeline по-прежнему блокирует media: A4 SQL revision publication/progress/bootstrap ещё не выполнен. Старый v1 handoff ниже остаётся примером форм, не новым runtime-профилем.

Решение пользователя 09.11.2026: при ошибке вложения анализируются пригодные успешные части, ошибки/покрытие сохраняются. Это утверждённая новая политика, ещё не реализованная нынешним CheckedAnalysis/Pipeline; старый полный отказ не отключать без revision/coverage/checkpoint перехода. Задачи и критерии — [NEXT_IMPLEMENTATION_TASKS.md](NEXT_IMPLEMENTATION_TASKS.md), A1/A3/A4. Live-протоколы не предоставляются по вебинару; ближайший показ media — strict synthetic/authored replay, не настоящая ASR/VL-проверка. Старые описания границ ниже относятся к прежнему коду.

Обновление U3 08.11.2026: PR #12/#13 уже в main `6f3b8df`. Постоянный ApprovedAsset теперь готовит read-only `app.media_runtime.resolve_registered_asset` по точным product/source/version/asset с owner/path/size/hash проверками; контракт/ошибки/Windows-приёмка — [MEDIA_RUNTIME.md](MEDIA_RUNTIME.md). Это закрывает серверную фабрику, описанную ниже как будущую; staging asset по-прежнему недолговечен. ASR/VL, Normalizer, media profile/replay и binary API остаются неготовыми. Исторические статусы 07.11 ниже сохранены.

Сверено 7 ноября 2026 года с общими схемами main `60eedf3` и локальным media import в `feat/u1-media-import`. Версия документа/примеров — `media-analysis-handoff-v1`; это не версия модели, prompt или ProcessingProfile. Для участников подготовлен контракт уже существующих DTO и границ подключения. Новых схем/API/модельных протоколов здесь не вводится; отдельного ответа U2/U3 о приёмке новой мультимодальной интеграции в этой сессии нет.

## Готовность и область передачи

В main есть общие MaterialInput/TicketInput, RegisteredMaterial/DatasetRegistration, SourceDetail/NormalizedDocument/Fragment/MaterialError, RecordingMeta, ProcessingProfile и GatewayResult/MediaText/ApprovedAsset. Локально реализованы безопасные файлы HTTP/CLI и recovery, проверены 652 backend-теста и ручной curl/CLI/restart. Текстовый AnalysisService подключается по утверждённому профилю и точному replay.

ASR/VL-адаптеров, runtime-подготовки persistent ApprovedAsset, мультимодального Normalizer/профиля и media request/recording registry пока нет. Pipeline до Normalizer выдаёт MEDIA_PIPELINE_NOT_IMPLEMENTED; TextNormalizer отдельно выдаёт MEDIA_NORMALIZATION_NOT_SUPPORTED для assets. Снимать эти ограничения можно только отдельной согласованной реализацией U1/U2/U3. Работа import не подтверждает распознавание.

## Источник и связь с материалом

TicketInput: `source_id`, `product_id=webneft`, timezone-aware `created_at`, исходный `text`, `requester_id|null`, список assets. Один MaterialInput: `asset_id`, `kind=audio|image`, простое `path`, `mime_type`, необязательный `source_url|null`. Допустимые MIME: audio/wav, audio/mpeg, image/png, image/jpeg; kind согласован с MIME. source_url хранится как недоверенные данные и никогда не скачивается. Ссылки/инструкции в обращении, изображении или звуке не дают модели доступ к файлам или инструментам.

RegisteredMaterial: `asset_id`, `kind`, `content_hash`, `storage_path`, `original_filename`, `mime_type`, `byte_size>0`. content_hash — SHA-256 фактических bytes; storage_path — серверный относительный путь от APP_UPLOAD_ROOT; original_filename — метка происхождения, не путь чтения. В DTO **нет** product_id/source_version, duration, width/height, URL выдачи, результата распознавания или признака «без ПДн». Duration/pixels проверены parser при импорте, но не сохраняются отдельными полями; если адаптеру они нужны, требуется проверенное чтение metadata либо согласованное расширение U3.

Контекст всегда передаёт `(product_id, source_id, source_version)`. Ключ зарегистрированного содержимого — `(product_id, asset_id, content_hash)`; asset_id без product/hash недостаточен. Сервер читает `Repository.get_source(source_id, product_id, version)` и `source_materials(source_id, product_id, version)`, связывает их по asset_id. Модели не выбирают source/version/путь. Ошибка чтения реестра не трактуется как пустой каталог материалов.

При повторе repository может оставить прежний canonical storage_path/оригинальную метку. Поэтому runtime-сопоставление не строится по original_filename, имени uploads-каталога или новому prepared-path. Полный registered_materials без фильтра используется U1 для сохранения файлов всех продуктов/исторических версий, а проверки reuse берут существующий product-specific view. Длительности/числа/идентичность не берутся из текста модели.

## Три разных hash и версии

| Значение | Источник истины и назначение |
|---|---|
| RegisteredMaterial.content_hash | SHA-256 bytes одного файла; проверяется до регистрации и перед использованием |
| SourceDetail.content_hash | Общий source_content_hash(ticket, materials), идентичность всей версии источника |
| normalization_hash | Хеш реализованного recipe нормализации/маскирования; часть ProcessingProfile и cache key |

Source hash учитывает обычные поля ticket и отсортированные asset_id/kind/MIME/source_url/byte-hash. Имя asset.path, server storage_path, dataset_id и время импорта не являются новой смысловой версией сами по себе. Разные source_id не дедуплицируются по одинаковому тексту/файлу. Изменение bytes даёт новую source_version; старые материалы/решения/ручная история сохраняются, ручная связь требует перепроверки по правилам Repository.

NormalizedDocument.version равен source_version, content_hash берётся из SourceDetail; его нельзя пересчитывать по masked-тексту или заменять hash файла. Текущий текстовый NORMALIZATION_VERSION=`text-pii-v1-proposal`, NORMALIZATION_HASH=`731c60015a270661e72a5a1d17c8c07bcce9306582fdec43ec3d226889ecf63a`. Media recipe изменяет семантику и требует нового реального hash/профиля; использовать текстовый hash как готовую мультимодальную версию нельзя. Старые profile/recordings/checkpoints не переписываются.

## Вход ASR/VL: только серверный ApprovedAsset

Существующий Python-тип в gateways.py:

```python
ApprovedAsset(asset_id: str, absolute_path: Path,
              content_hash: Sha256, mime_type: str)
AsrGateway.transcribe(asset: ApprovedAsset) -> GatewayResult[MediaText]
VisionGateway.describe(asset: ApprovedAsset) -> GatewayResult[MediaText]
```

Тип сам по себе не проверяет файловую систему. Сервер перед вызовом обязан заново связать зарегистрированный материал с нужной версией источника, проверить разрешённый root/конечный путь, отсутствие небезопасных links, обычный файл, size/hash и доступность. original_filename/source_url/модельный вывод не используются как absolute_path. У2 не конструирует ApprovedAsset из входного JSON и не читает произвольные файлы.

Важно: ApprovedAsset, который U1 создаёт при проверке staged upload, относится к **временному** файлу. Он не возвращается как долговечная runtime-ссылка и после commit/cleanup непригоден. Для обработки нужно заново подготовить ApprovedAsset из `source_materials` и окончательного зарегистрированного файла. Фабрика такого runtime-контекста остаётся совместной задачей U1/U3; документ не изображает её уже реализованной функцией.

Адреса, ID, авторизация и протоколы внутренних Qwen-ASR/Qwen-VL не предоставлены/не проверены. Наличие Gateway Protocol не означает OpenAI-совместимость или готовый live. Бинарные файлы модели получает только серверный адаптер; секреты остаются на сервере. Синтетика или отдельно разрешённые данные обязательны: маскирование текста не обезличивает исходные pixels/звук.

## Выход шлюза и вход U2

`GatewayResult` содержит только `value` и `metadata`. Для ASR/VL value — MediaText с `fragments`; не добавлять туда product/source/path/счётчики/бизнес-статус как несуществующие schema fields. Fragment включает `fragment_id`, `asset_id|null`, `kind`, `text`, парные `start_ms/end_ms|null`, `warning|null`.

Для исходного текста kind=text и asset_id=null. Для аудио kind=asr с соответствующим audio asset_id. Для изображения kind=vision с соответствующим image asset_id. Только ASR допускает парные целые timestamps ≥0 с end>start; при отсутствии реальных меток оба null. Метки не выдумываются из длительности/имени файла. Связность с фактическим аудио и границы интервала проверяет будущий серверный адаптер/Normalizer, схема Fragment сама duration не знает.

ASR/VL текст маскируется до сохранения NormalizedDocument, до textual recording и любого текстового анализа. В live дальше используется тот же обработанный ответ. Текущее mask_pii не гарантирует имена/адреса/произвольные форматы — warning сохраняется. Не менять оригинал источника и не считать его полностью обезличенным.

Будущий Normalizer возвращает NormalizedDocument со стабильным source context, локально уникальными fragment_id и явными errors. Pipeline назначает окончательные детерминированные fragment_id до U2.extract_needs. U2 получает только этот зафиксированный masked документ; цитата обязана входить именно в соответствующий fragment.text, source/fragment не выбираются из другого документа. Несколько потребностей на источник разрешены; постоянные need/topic ID и статистику назначает сервер.

Сбой шлюза передаётся существующим GatewayUnavailable(code, safe_message), а ошибка материала сохраняется как MaterialError(source_id, asset_id, stage, code, message). Конкретные ASR/VL коды и частичное покрытие требуется согласовать при реализации. В MediaText сейчас нет поля errors. Успех с пустым текстом, отсутствие речи и ошибка распознавания — разные состояния; ошибка не превращается в `needs=[]`. Текущий CheckedAnalysis отвергает ошибочный документ; U3/U2 должны согласовать partial-material policy, checkpoint/coverage и retry до её изменения. Нельзя просто убрать media-guard и объявить путь готовым.

## Provenance: независимые признаки

| Признак | Что означает |
|---|---|
| SourceDetail.synthetic | Искусственные данные; false само по себе не разрешает реальные материалы |
| SourceDetail.data_origin / DatasetRegistration.origin | imported либо authored — происхождение источника/наборa |
| Run.mode | live либо replay — способ обработки, выбирается сервером |
| RecordingMeta.origin | model либо authored — происхождение сохранённого ответа; imported здесь недопустим |
| Analysis origin/mode | Не выводятся из того, что файл импортирован или запись воспроизведена |

Один источник может быть synthetic=true/data_origin=imported, а ответы в replay — authored. Реально записанный model-ответ может воспроизводиться в replay, что не превращает replay в измерение живой модели. В live authored-ответ не подставляется как модельный, автоматического fallback нет.

RecordingMeta имеет `request_hash`, `prompt_hash|null`, `normalization_hash`, `model_id`, `parameters`, `origin`, `recording_set_version`. Метаданные задаёт и проверяет адаптер/сервер, не модель. Prompt hash при наличии — SHA-256 реального UTF-8 текста; normalization hash соответствует фактическому recipe; параметры/модель/версии/режим участвуют в проверенной политике ключа. Нельзя заполнять неизвестные поля фиктивными ID/hash и выдавать их за реальный media request.

Текущий TextRequest/replay registry поддерживает только extract_needs/assign_topic/propose_topic. Media request hashing, модельные schemas/параметры, запись/проверка media replay и происхождение подтверждает будущий срез U3 совместно с U1/U2. Бинарные bytes не помещаются внутрь recording: нужны hash и ссылка на разрешённый synthetic fixture. Текстовые поля record маскируются; неизвестный вход даёт явную ошибку, не «похожую» запись. Media ProcessingProfile и RecordingMeta в передаваемом примере оставлены null.

## Проверяемые примеры

[data/contracts/media-analysis-handoff.json](../data/contracts/media-analysis-handoff.json) содержит четыре формата с реальными SHA-256/byte_size существующего synthetic набора, валидные SourceDetail/RegisteredMaterial/Fragment/NormalizedDocument/MaterialError. Пути хранения иллюстративны и не являются существующим реестром. MediaText/NormalizedDocument — authored **примеры формы**, не результаты распознавания; media text пуст и явно помечен. `normalization_version=media-contract-example-not-implemented` нельзя использовать как runtime-профиль.

Чтение/проверка примеров без моделей и записи БД из backend:

```sh
../.venv/bin/python -X utf8 -m scripts.check_media_handoff
```

Ожидается `media-analysis-handoff-v1: 4 examples valid; hashes/source binding/provenance checked; runtime media profile=not configured; models_called=false`. Этот срез документирует текущие общие структуры; не меняет schema/API, prompts, replay, pipeline или зависимости.

## Что нужно для следующей общей обработки

U1 готовит проверенные внутренние ASR/VL-адаптеры и мультимодальный Normalizer, включая masking/errors и реальные limits. U3 подключает persistent ApprovedAsset resolution, profile/recipe version, media request/recording/checkpoints, retry/coverage и guards в одном процессе. U2 подтверждает source/fragment/evidence/partial-error вход и запускает оценку на согласованных метках. До этого текущие явные отказы сохраняются. U4 отдельно подключает binary-source API/просмотр, когда они появятся.

Приёмка будущего среза должна показать четыре формата, реальные либо явно replay распознавания, точные masked-цитаты, отсутствие выдуманных timestamps, ошибки отдельных материалов, повтор/restart и сохранность решений. Это ещё не выполненный live-тест или согласование неизвестного модельного протокола. Сам импорт, recovery и API U4 описаны в [U1_MEDIA_HANDOFF.md](U1_MEDIA_HANDOFF.md).
