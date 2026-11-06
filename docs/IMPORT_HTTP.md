# Текстовый импорт через HTTP и CLI

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
