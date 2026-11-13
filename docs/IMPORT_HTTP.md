# Импорт текста и медиа через HTTP и CLI

## Текущий срез U1 — 2026-11-07

Общий `import_tickets` поддерживает текст и проверенные WAV/MP3/PNG/JPEG. HTTP POST `/api/v1/imports?dry_run=true`, POST `/api/v1/imports`, GET `/api/v1/imports` и прежние ImportPreview/ImportResult/ErrorResponse сохранены. CLI `app.cli import --manifest … [--dry-run]` читает перечисленные простые имена только внутри каталога manifest и вызывает тот же сервис. Схемы, SQLite v3 и source_content_hash не менялись. Реализация и результаты — [Техническая документация](U1_MEDIA_HANDOFF.md), передача — [U1_MEDIA_HANDOFF.md](U1_MEDIA_HANDOFF.md).

Manifest по-прежнему содержит ровно dataset_id/synthetic/tickets. HTTP передаёт один manifest (поле либо file-part) и повторяющиеся files с точными именами из assets.path; порядок частей произвольный. Имя manifest не используется как путь. Имена медиа должны быть NFC, без каталогов/управляющих символов/Windows reserved names; коллизии casefold отвергаются. В CLI соседние файлы, не перечисленные в manifest, не читаются. Отсутствующие/лишние переданные файлы, unsafe links, неверные сигнатуры/метаданные и MIME manifest отклоняют весь набор. MIME клиента не является доказательством; bytes проверяются wave, Pillow и mutagen.

Размеры определены в двоичных единицах: МиБ=1 048 576 байт. Константы media/body находятся в `app/security.py`, manifest/tickets/depth — в прежнем importer:

| Ограничение | Значение |
|---|---|
| Тикеты / manifest / JSON depth | 100 / 1 048 576 байт / 32 |
| Медиа / сумма их байтов | 30 / 52 428 800 байт (50 МиБ) |
| Фактическое HTTP-тело | 54 525 952 байт (52 МиБ) |
| WAV/MP3 | 10 485 760 байт, до 60 секунд |
| PNG/JPEG | 5 242 880 байт, до 25 000 000 пикселей |
| Multipart parts / file-parts / fields | 31 / 31 / 1, включая manifest |
| Заголовки части / boundary / chunk | 8 192 байта / 70 ASCII-байт / 32 768 байт |

HTTP ограниченно сохраняет сырой конверт в собственный временный файл **до создания multipart parser**, затем разбирает его порциями с отдельными ограничениями файлов. Content-Length не заменяет фактический счётчик. Тело не накапливается в 52-МиБ bytearray; Request.form/UploadFile не используются. После parser.finalize обязателен on_end. Лимиты image/audio и суммы действуют также при записи staged-файлов. Дополнительный дисковый проход ограничен: сырой конверт + extracted media до 102 МиБ, резерв со служебным JSON около 104 МиБ на запрос.

Preview создаёт только собственные временные файлы и после нормального завершения убирает их; uploads/owner marker/SQLite/import_log не меняются. Оно не сохраняет сессию подтверждения, не резервирует counts и не вызывает моделей. Commit — новый полный запрос с новым серверным UUID и повторной проверкой. Браузер должен пересоздать FormData с текущими файлами и сбросить preview при смене выбора. При потере ответа новый запрос безопасен и может вернуть unchanged.

Staging: `APP_TMP_ROOT/imports/<request_uuid>/`; final: `APP_UPLOAD_ROOT/requests/<request_uuid>/<file_uuid>.<extension>`. Клиентские имена, source/asset/dataset ID в server-пути не включаются. Корни должны быть раздельными и на одном диске; файловая система должна поддерживать используемый атомарный hard-link transfer (проверено APFS, Windows runtime не проверен). Новые final-файлы размещаются до короткого register_dataset под прежним import_commit, хеш проверяется до SQLite commit. После регистрации удаляются только доказанно собственные пути, которых нет в полном registered_materials всех продуктов/версий. Новая лишняя копия не заменяет старый канонический файл.

Журнал фиксирует planned-пути и идентичность эксклюзивно созданного final-каталога. При неопределённом commit/нечитабельном реестре/подозрительном журнале файлы сохраняются для сверки. До приёма HTTP startup после initialize/interrupt_active_runs делает recovery; проблемы видны в `health.integration_errors` и локальных `var/reports/import-recovery-*.json` с asset ID/кодами. Непустое legacy storage без owner-маркера и roots другой БД автоматически не присваиваются. Один DB-path соответствует одной паре roots; перенос/замена БД требует проверки принадлежности. Media CLI запускается при остановленном сервере и других CLI: guard process-local. CLI dry-run делает только read-only сверку; после прерывания внутри SQLite сначала нужен штатный startup/init-db существующей БД для восстановления hot journal, без сброса данных.

Ошибки набора: preview 200 invalid/can_commit=false, commit 422. Размеры/количество — 413 в обоих режимах; query — 422; RUN_ACTIVE/IMPORT_ACTIVE/registration conflict — 409; недоступное storage/целостность — 503. `IMPORT_CLEANUP_PENDING` означает необходимость проверить результат: DB commit мог уже пройти, повтор безопасен, зарегистрированные файлы не удаляются. Все сообщения безопасные и русские; первая ошибка даёт invalid=1, не partial import и не обещание полного списка нарушений. Отсутствие БД в текстовом preview сохраняет прежний invalid-ответ без создания БД.

Зафиксированы Pillow==13.0.0 и mutagen==1.48.1 по разрешению пользователя выбрать и проверить pins; остальные 25 версий сохранены. Установка/полный pytest выполнены на macOS ARM64/Python 3.13.0: **652 passed, 1 прежний warning**, pip check без конфликтов, общие/HTTP/proposal/workflow-примеры валидны. Реальные HTTP/CLI/restart и отдельные process-stop recovery прошли. Windows, frontend/браузер и live-модели не проверялись. MP3 разбирается как контейнер/метаданные, полное декодирование каждой возможной порчи не гарантируется; PNG/JPEG проверяются verify+load. Распознавание, медийный replay и binary GET assets не добавлены; Normalizer/Pipeline сохраняют явные media-отказы.

Безопасное воспроизведение из backend:

```sh
../.venv/bin/python -X utf8 -m scripts.check_media_import
../.venv/bin/python -X utf8 -m pytest -q
../.venv/bin/python -m pip check
```

Скрипт использует отдельные SQLite/uploads/tmp и свой localhost, рабочая БД не затрагивается. Синтетические данные — [data/media-import](../data/media-import/README.md); обновлённые transport-примеры — `data/contracts/import-http-examples.json`. Точные report-paths и команды сохранены в ExecPlan.

Ревью перед передачей: повреждённый owner/реестр возвращает безопасный 503, чрезмерно вложенный journal сохраняется с ошибкой recovery; проверка повторного материала ограничена текущим продуктом, cleanup по-прежнему читает весь реестр. Для переносимости отвергаются также reserved basenames с пробелом перед расширением. Ручные curl/CLI подтверждены отдельно от скрипта: preview/commit/repeat, 422/503 без изменения SQLite, delta, решение и restart. Команды и ожидаемые ответы — [MEDIA_IMPORT_CHECK.md](MEDIA_IMPORT_CHECK.md).

## Архив исходного текстового среза 2026-11-06

Следующие абзацы сохраняют историю проверки text-only реализации. Старые 1 МиБ+64 КиБ/8 parts и запрет files ниже не являются текущими лимитами; актуальные правила приведены выше.

Реализовано 2026-11-06 на основе готового import_tickets, ветка feat/text-import-api от 72606af. Она зависит от предыдущего U1-среза feat/u1-text-integration, ещё не слитого в main. Эта задача не добавляет frontend, media, SQL/DTO/guard, модели или конфигурацию анализа.

Пользователь передал подтверждение участника 3: согласованы imports_api/main/cli, существующие модели ответов/статусы, указанные ниже лимиты и единственное добавление python-multipart==0.0.33 в requirements.in/requirements.txt. Другие закреплённые версии не обновлялись. Версия проверена по [PyPI](https://pypi.org/project/python-multipart/0.0.33/); используется низкоуровневый [streaming parser](https://multipart.fastapiexpert.com/), без parse_form/UploadFile/spooling.

## Контракты для участника 4

| Запрос | Успех | Ошибка |
|---|---|---|
| POST /api/v1/imports?dry_run=true | 200 ImportPreview: counts/errors/warnings/can_commit | Структура/JSON: 200 invalid preview, can_commit=false; размер: 413 ErrorResponse |
| POST /api/v1/imports (или dry_run=false) | 200 ImportResult с окончательными counts и imported_at, только после commit | Невалидный набор/конверт: 422; размер: 413; активный run: 409 RUN_ACTIVE; ошибка БД: 503 |
| GET /api/v1/imports | 200 list[ImportLogEntry] завершённых регистраций webneft, в порядке регистрации | Ошибка чтения БД: 503; неизвестные query-параметры: 422 |
| POST /api/v1/runs во время фиксации импорта | Существующий контракт запусков | 409 IMPORT_ACTIVE от существующего общего guard |

POST принимает multipart/form-data: ровно один manifest как файл или UTF-8 поле, повторяющиеся files предусмотрены контрактом для будущих медиа. Сейчас **любые files, даже пустые, и assets внутри JSON отклоняются целиком**: preview invalid или commit 422 MEDIA_NOT_SUPPORTED, текст частично не импортируется. Неизвестные поля multipart и дубли manifest также отклоняются. Имя загруженного manifest не используется как путь, файл на диск не сохраняется.

JSON-оболочка общая с Python-сервисом: dataset_id, честный synthetic boolean, tickets; product_id=webneft, даты с timezone, source/asset уникальны. Повторные JSON-ключи/неизвестные поля/неправильный UTF-8 и глубина проверяются прежним общим валидатором. Браузер эти правила и content_hash не повторяет. Синтетический manifest: data/demo/manifest.json. Примеры ответов: data/contracts/import-http-examples.json, явно synthetic/authored/contract-examples-only. Timestamp/UUID в примерах иллюстративны, это не выполненный импорт и не результат модели.

Пример отправки из браузера:

    const form = new FormData();
    form.append('manifest', manifestFile);
    const response = await fetch('/api/v1/imports?dry_run=true', {method: 'POST', body: form});

Content-Type вручную не устанавливать: браузер добавляет boundary. Для подтверждения создать новый FormData с тем же текущим файлом и POST /api/v1/imports. Сессии preview/import_id/токена подтверждения нет. Изменившийся после preview файл и изменившееся состояние БД проверяются полностью заново. Предварительные counts не резервируются. Без предварительного preview фиксация также разрешена. Каждый HTTP commit получает новый серверный UUID; повтор unchanged добавляет завершённую запись запроса, но не версию/дубль источника.

Статусы и пути из ExecPlan сохранены. Ответы ошибок используют ErrorResponse (code/message); они не содержат исходный текст/ValidationError/имя файла. Размер — 413 в обоих режимах, не invalid preview. Ошибочные query-параметры всегда 422: разрешён только один dry_run=true/false, идентификатор регистрации и product_id клиентом не задаются. GET не принимает query-параметров. Схемы доступны в существующем /openapi.json, включая multipart и union существующих ImportPreview/ImportResult.

UI принадлежит участнику 4: показать preview и can_commit, затем фактические counts сервера, состояния ожидания и русские ошибки; не подтверждать сохранение до ответа commit. После потери ответа повтор безопасен. Не включать кнопку анализа лишь по успешному preview. При обычной неподключённой конфигурации анализ по-прежнему возвращает INTEGRATION_NOT_READY: успешный импорт не обещает доступность моделей/полного анализа. Этот 503 относится к /runs, не мешает /imports и не должен скрываться.

## Ограничения до разбора и во время чтения

Общий предел тела — 1 114 112 байт (1 МиБ + 64 КиБ). Request.stream считается по фактическим байтам, независимо от отсутствующего/ложного Content-Length. При превышении чтение останавливается и возвращается 413 **до создания multipart parser**. Фиксированный малый текстовый предел позволяет сначала ограниченно прочитать весь конверт в память; не использовать эту стратегию без пересмотра для больших медиа.

После проверки всего тела parser.write получает порции не более 32 КиБ. Ограничения callback до копирования данных: manifest/каждая часть 1 048 576 байт, не более 8 частей/8 полей/8 файлов, суммарные заголовки каждой части до 8 КиБ, boundary до 70 печатных ASCII-байт. Счёт файлов включает manifest-file. Эти верхние пределы не разрешают лишние части: корректен только один manifest. На неподдержанные/повторные поля фиксируется ошибка всего конверта; их данные не накапливаются, но лимиты продолжают проверяться. Никогда не распаковывать архив/вложенный multipart и не декомпрессировать вход.

Request.form/UploadFile не применяются: в закреплённой Starlette 0.46.2 max_part_size ограничивает обычные поля, а файлы могут начать spooling раньше лимита. Здесь нет даже временных файлов загрузки; одновременно ограничены тело и manifest в памяти. Предупреждения parser не выводятся в глобальный лог: выделенный per-parser logger подавляет сырые библиотечные сообщения, клиент получает безопасный код/действие.

В python-multipart 0.0.33 finalize() не удостоверяет наличие закрывающей границы. Обязателен callback on_end; оборванный конверт не достигает import_tickets и не создаёт регистраций. После полной транспортной проверки API передаёт **сырые bytes manifest** в общий import_tickets, сохраняя строгую UTF-8 проверку, а не декодируя текст форм с latin-1 fallback.

Результат service/чтение истории выполняются в threadpool, поэтому ожидание существующего SQLite/guard не блокирует event loop. Нового guard нет: RUN_ACTIVE/IMPORT_ACTIVE и регистрация с commit остаются Repository. Preview не захватывает guard фиксации и может читать во время run; counts предварительны, commit при активном run всё равно получает 409. При ошибке до DB commit — откат всей регистрации; после commit потеря HTTP-ответа не откатывает импорт.

Обычный startup FastAPI инициализирует свою БД как прежде, до запросов. Ни POST preview/commit, ни CLI import не вызывают initialize/startup ради регистрации, не создают таблиц и не обращаются к моделям. Для CLI и Python нужен уже подготовленный SQLite v3; отсутствие БД явно даёт ошибку, не пустую базу.

## CLI

Из backend самостоятельного клона со своим .venv:

    ../.venv/bin/python -m app.cli import --manifest ../data/demo/manifest.json --dry-run
    ../.venv/bin/python -m app.cli import --manifest ../data/demo/manifest.json

PowerShell: ..\.venv\Scripts\python.exe -X utf8 с теми же аргументами. APP_DB_PATH интерпретируется от корня репозитория, путь --manifest — от текущей папки. Явно выберите тестовую БД, если проверяете команды вручную; основной безопасный вариант ниже сам изолирует БД.

CLI читает не больше MAX_MANIFEST_BYTES+1, вызывает тот же import_tickets и печатает JSON существующей модели. Exit 0 — valid preview/успешный commit, exit 1 — invalid preview/ошибка чтения/валидации/БД, некорректный синтаксис команды — exit 2. --dry-run/--manifest нельзя передать init-db или другой команде и молча проигнорировать. CLI не создаёт отсутствующую БД. ID запроса не принимается от файла/CLI.

## Выполненная проверка

test_imports_api.py/test_import_cli.py проверяют реальные маршруты/схемы, неизменность bytes БД/журнала после preview, commit без preview и повтор с новой квитанцией, устаревший preview, неправильные MIME/envelope/UTF-8/JSON/media, header/part/field/file/body пределы, ложный Content-Length/поток, границы UTF-8 между parser chunks, disconnect, отсутствие spooling/init/model-вызовов и утечек текста. Проверены 503 до commit с откатом, потеря ответа после commit с unchanged на повторе, RUN_ACTIVE/IMPORT_ACTIVE, CLI subprocess/missing DB и существующие JSON DTO-примеры. На macOS/Python 3.13.0 получены 64 профильных и 433 backend-теста, одно прежнее предупреждение Starlette/AnyIO; 16 общих/7 прежних HTTP-примеров и pip check прошли. Новые 9 import HTTP-примеров проверены по существующим DTO.

scripts.check_import_http запускает настоящий Uvicorn на случайном 127.0.0.1-порту с новой SQLite, выполняет httpx HTTP и отдельный CLI subprocess, затем рестартует сервер. Рабочие БД/серверы не используются. Topic/grounding создаются диагностикой вручную и явно authored только для проверки решения; исходники остаются imported/synthetic, seed-demo не трогается, модель не вызывается.

Фактический ручной отчёт: var/import-http-check-5a3a1e5d98c1433ba8282c5d23a781ac/result.json. Пройдены 200 preview без записи → 200 inserted=4 → 200 unchanged=4, 422/invalid preview для ошибок/медиа, настоящий chunked oversize без Content-Length → 413, 409 RUN_ACTIVE/IMPORT_ACTIVE, обновление 1/3 с сохранением source history/ID/решения/истории и reopen после рестарта. Анализ остаётся честно 503 INTEGRATION_NOT_READY. Windows и браузерная форма не запускались.

Повторение из backend:

    ../.venv/bin/python -m pytest -q tests/test_imports_api.py tests/test_import_cli.py
    ../.venv/bin/python -m scripts.check_import_http
    ../.venv/bin/python -m pytest -q

Во вложенном клоне текущей сессии использован интерпретатор той же машины ../../../../.venv/bin/python. Установлен только согласованный python-multipart==0.0.33, .venv не переносилось, остальные версии не обновлялись. Каждая процедура создаёт новый var/import-http-check-<uuid>, прежние отчёты и данные не удаляет.

Ключевые строки ожидаемого и полученного HTTP-прогона (UUID отличается):

    preview: 200 valid inserted=4; database/history=unchanged
    commit/repeat: 200 inserted=4 -> 200 unchanged=4; completed_imports=2
    invalid/media: 422 or invalid preview; chunked oversized body: 413; no partial import
    coordination: 409 RUN_ACTIVE; 409 IMPORT_ACTIVE; guards=reused
    delta: 200 updated=1 unchanged=3; source_history/topic_id/decision/history=preserved
    PASS: actual HTTP/CLI/restart; old decision/history preserved; analysis remains honestly unavailable

Финальная сверка: защищённые существующие U1-core/U2, DTO/storage/pipeline/config и старые data не изменились; все 6 772 прежних файла данных исходной копии совпали по SHA-256. Изменения прежних source-файлов — только согласованные main.py/cli.py; версия единственного нового parser закреплена без обновления прочих зависимостей. Отчёт var/reviews/http-import-preservation-result.json. Ветка feat/text-import-api; commit/push/merge этого этапа не выполнялись.

Последующий механизм обычной runtime-сборки описан в RUNTIME_PROFILE.md. Без реальной согласованной конфигурации /runs остаётся 503, при совпадающем явном профиле обычный main.app теперь может подключить replay; никакой зависимости импорта от этой готовности не добавлено.

Повторное ревью перед передачей 2026-11-06: полный текущий backend — 459 passed, одно прежнее предупреждение. Реальный HTTP/CLI/restart повторно прошёл, отчёт var/import-http-check-8a73166ff0f6435782907a9702fa9836/result.json. Ошибки/лимиты/блокировки и сохранность решения подтверждены; производственная БД не использовалась.
