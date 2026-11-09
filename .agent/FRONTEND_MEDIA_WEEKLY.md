# Подключить оставшиеся экраны У4 к готовому backend

Живой ExecPlan по `.agent/PLANS.md`. Пользователь 09.11.2026 согласовал взять часть У4 и сначала опубликовать проверенный backend. Коммит ac3ae94 отправлен в origin/feat/weekly-partial-media, merge делает пользователь. Работа UI — отдельная feat/frontend-media-weekly от этого коммита, без клона/сброса/изменения .env/SQLite.

## Purpose / Big Picture

Пользователь сможет импортировать synthetic JSON с WAV/MP3/PNG/JPEG через preview→commit→repeat, открыть зарегистрированный файл/ошибку, увидеть покрытие/категории/приоритеты и сформировать/скачать сводку завершённой MSK недели. Все действия используют готовые API, не браузерные модели/keyword classification. Существующие формы текста/review/решений/паспортов и черновики сохраняются.

## Progress

- [x] (2026-11-09, публикация) Пользователь отдельно поручил commit/push готового UI-среза в feature-ветку. Merge в main не поручен; текущий diff и secret/private checks проверяются перед публикацией, результаты Git сообщаются отдельно.
- [x] (2026-11-09) Проверен staged diff/70 файлов/секреты, пользовательский org_feedback сохранён побайтно; backend и документы закоммичены/отправлены ac3ae94. Ветка UI создана отдельно; merge main не выполнялся.
- [x] (2026-11-09) MediaImportPanel: raw JSON/File snapshot, preview invalidation/abort, двойной commit/uncertain guard, journal/source verification; прежняя квитанция не связывается с новым неуспешным набором. Текстовая форма сохранена.
- [x] (2026-11-09) Processing/assessment summary/week readers проверяют source/version/hash/revision/topic_version/units/counts; binary download ограничен фактическими bytes и MIME; UTF-8 report проверяется, legacy read fields совместимы.
- [x] (2026-11-09) SourceViewer/MediaAsset/SourceProcessing, TopicAssessments, новые run counters и навигация; unknown/missing/recheck/errors видимы. Нет source_url/path fetch, автозагрузки всех media или keyword classification.
- [x] (2026-11-09) WeeklyReport: серверный период, явная генерация/скачивание, stale error handling, plain text без HTML/scripts; Object URLs освобождаются.
- [x] (2026-11-09) 284 frontend tests, TypeScript/build/lint прошли; четыре настоящих React/jsdom→HTTP→SQLite→React сценария: var/frontend-media-http-c2e56844c7b1455da12f47822e72fe75/result.json. Изолированный demo подготовлен и backend8000/frontend5173 ответили200, собственные процессы остановлены. Документ docs/FRONTEND_MEDIA_WEEKLY.md содержит команды.
- [x] (2026-11-09) Финальный audit var/reviews/frontend-media-weekly-result.json: private5, backend runtime/pins, старые text/review/decision/passport формы и source/archive сохранены; credential scan/diff чисты. На UI ветке ничего не staged/committed/pushed, HEAD=ac3ae94.
- [ ] Визуальная приёмка: попытка через Browser Use завершилась The browser access host is unavailable. Нативный Windows маршрут ранее блокировался на URL-проверке; обход не выполнялся. HTTP/jsdom не объявляются визуальной проверкой. Пользователь может выполнить приведённый двухтерминальный показ.

## Context and Orientation

Backend контракт — docs/PARTIAL_WEEKLY_HANDOFF.md, schemas server-completion.json. frontend/src/api.ts — единый HTTP слой. Текущий ImportPanel — проверенный text-only поток с uncertain receipt/journal; не заменять его wholesale. Добавить отдельный MediaImportPanel, переиспользуя API и onImported для обновления processing gate. SourceViewer уже показывает fragments/errors; встраивать новые панели с независимыми ошибками, чтобы они не скрывали старые sources/решения. Backend /processing может вернуть 404 для legacy text: это отсутствие новой ревизии, а не отсутствие потребности.

## Milestones and Concrete Steps

Сначала расширить read API и optional FormData files, не меняя старые сигнатуры текстового импорта. Новый media preview сохраняет raw manifest и выбранные File объекты; commit пересоздаёт FormData из того же snapshot. Сервер остаётся единственным окончательным валидатором signature/metadata/hash. Клиент проверяет только понятные пользователю явные ошибки/ограничения и не пересобирает JSON, скрывая дубли ключей.

Далее добавить только явно запрошенную загрузку media в SourceViewer. Проверять returned MIME/ограниченный размер/точную версию; никакого source_url/path. Object URLs создаются из ответа локального сервера и отзываются при смене/закрытии. Ошибки/несинтетический запрет видны; прежний объект при stale ошибке не выдаётся актуальным.

Processing/assessment readers проверяют source/version/digest/topic_version и сохраняют ошибки отдельно от карточки. Unknown и отсутствующая оценка различаются, category/priority не подменяют бизнес-статус. Для run новые source/material units подписаны отдельно и не интерпретируются как качество модели.

Недельная панель получает период и Markdown по кнопке. Markdown показывается plain text, включая недоверенные заголовки, без dangerouslySetInnerHTML. Скачивание — Blob текущего успешного ответа, при ошибке обновления старый отчёт помечается неактуальным. Нет UI обещания scheduler/live.

Из frontend (Node из обычного PATH либо существующего var/tooling/node/active-path.txt):

    npm run test -- --run
    npm run lint
    npm run build

Из backend повторять затронутые integration scripts и schema tests. Не обновлять pins. Проверить races/abort, отсутствие двойного commit, смену files после preview, raw exact multipart, неверный MIME/403/503, unknown assessment и stale week/report. Browser-click — только доступным разрешённым инструментом; предыдущий Computer Use блокировался на Windows URL. Не обходить и не объявлять HTTP визуальной приёмкой.

## Idempotence and Recovery

UI изменения обратимы и не мигрируют рабочие данные. Preview не сохраняет разрешение на сервере, commit имеет собственную повторную валидацию. Потеря ответа не считается отказом записи: журнал/источники и явное подтверждение пользователя перед повтором. Abort при смене поля не отменяет уже принятый commit, потому поля блокируются во время записи. Новые draft данные не пишутся в localStorage. Media Blob URLs освобождаются. Backend commit уже опубликован; UI новый commit/push автоматически не подразумевается без проверки/отчёта.

## Surprises & Discoveries

Media API поддерживает audio ranges, но UI для явной ограниченной загрузки может использовать проверенный Blob с native controls: это не требует открывать произвольный оригинальный URL. Server remains authority. Старые read responses могут не иметь processing fields, новые отсутствующие оценки не должны скрывать прежний источник.

Review выявил риск связывания прежней receipt с новыми pending IDs: раздельные confirmedIds/pendingIds и regression закрывают его. jsdom Blob не предоставляет полный browser Blob.text API, поэтому HTTP download читает bounded bytes и декодирует Markdown явно через fatal UTF-8; тот же код работает с реальным HTTP. Portable active-path.txt оказался UTF-8 BOM: dev-процедура читает utf-8-sig, системный PATH/политики PowerShell не менялись.

## Decision Log

09.11.2026, пользователь/У3: взять согласованную часть У4. Backend milestone опубликован отдельно, UI отдельной веткой; пользователь делает merge. Добавить функциональность текущего стека без дизайна/redesign/зависимостей и без переписывания работающих text/review форм.

## Outcomes & Retrospective

Функциональная часть У4 выполнена в новой ветке; backend checkpoint ac3ae94 опубликован отдельно и merge оставлен пользователю. 284 unit/component/API tests, build/lint и четыре сетевых сценария прошли. Backend runtime/pins/рабочие private файлы не менялись, fixtures/runtime разделены. UI изменения не закоммичены/не pushed этим этапом. Визуальный browser-host недоступен; честное ограничение и ручной показ записаны в документации. Далее нужны визуальная приёмка/удобство, разнообразный synthetic demo/разметка и финальная упаковка команды.

Обновление 09.11.2026: создан план перед реализацией UI по согласованию с У4 через пользователя.

Обновление 09.11.2026 после реализации: добавлены фактические результаты/HTTP proof и закрытые races; визуальный блокер выделен отдельно, чтобы не подменять browser приёмку тестами.
