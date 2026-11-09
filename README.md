# GPN Insight

Итоговая приёмка 09.11 после PR #17/#18: [проверки У3/У1, уточнение обезличенности и стабильный показ](docs/FINAL_ACCEPTANCE.md). Новый изолированный показ: из backend `..\.venv\Scripts\python.exe -X utf8 -m scripts.prepare_acceptance_demo`. Пять synthetic источников, три темы, повторяемость, review, partial/retry и сохранённое решение; только строгий authored replay. В источнике добавлен возраст от даты создания, без трактовки как SLA.

UI media/weekly 09.11: подключены preview/commit/repeat с файлами, просмотр synthetic медиа, покрытие/оценки/приоритеты и формирование недельного отчёта. [Пошаговый показ в двух терминалах](docs/FRONTEND_MEDIA_WEEKLY.md). UI-срез ведётся в feat/frontend-media-weekly от опубликованного backend ac3ae94; merge в main выполняет пользователь.

Срез 09.11 A4–A7: подключены partial/media replay Pipeline, SQLite v4 с backup/историей ревизий, категории/приоритеты с evidence, сводка завершённой MSK недели и безопасная выдача synthetic медиа. [Контракты и команды команды](docs/PARTIAL_WEEKLY_HANDOFF.md). Быстрая изолированная проверка из backend: `..\.venv\Scripts\python.exe -X utf8 -m scripts.check_partial_weekly_integration`. Live и новые UI media/report экраны не объявлены готовыми; нижние описания прежних срезов исторические.

A1–A3 от 09.11: [partial/media contracts, Normalizer, strict replay и политика originals](docs/PARTIAL_MEDIA_FOUNDATION.md) реализованы как отдельная проверяемая связка. 728 backend tests/4 Windows symlink skip, 258 frontend tests/build; прежние SQL/prompts/pins сохранены. Production media Pipeline подключается следующим A4, live недоступен. Из backend: `..\.venv\Scripts\python.exe -X utf8 -m scripts.check_partial_media_foundation` — безопасная synthetic процедура partial→retry/reopen, не рабочая БД.

Интеграция 09.11.2026: PR #15 У4 уже включён из GitHub main `620b741` в текущую `feat/weekly-partial-media`, без нового клона. Text import/preview/processing и review attach/insufficient_data готовы. Утверждённые категории/приоритеты, partial processing и завершённая неделя MSK разбиты на [конкретные следующие задачи У3/У1](docs/NEXT_IMPLEMENTATION_TASKS.md); эти новые backend-функции ещё не объявляются реализованными.

План от 09.11.2026 скорректирован по вебинару и task.pdf: [общий ExecPlan](.agent/EXECPLAN.md), [полный разбор ответов и вопросы команде](docs/ORG_FEEDBACK_REVIEW.md). Ближайшие 2–3 дня — прототип с медиа по возможности, затем общие материалы/удобство. Доступ к моделям не предполагается; категории/приоритеты/недельная сводка — следующие согласуемые задачи, не уже готовые функции. Исторический план сохранён отдельно; код в этой документальной задаче не менялся.

Состояние на 08.11.2026: PR #12 (медиаимпорт U1) и #13 (фильтры/редактор паспорта U4) объединены в main `6f3b8df`. Серверная подготовка постоянных медиа, Windows-проверки, новый handoff v2 и команды — [MEDIA_RUNTIME.md](docs/MEDIA_RUNTIME.md). Старые этапные статусы ниже исторические; resolver не включает ASR/VL или media pipeline.

Передача U2/U4: [контракт media-источников, metadata и provenance](docs/MEDIA_ANALYSIS_HANDOFF.md), [передача импорта U1](docs/U1_MEDIA_HANDOFF.md). Текстовый и media import уже в main. Из `backend/`: `../.venv/bin/python -X utf8 -m scripts.check_media_handoff` проверяет четыре authored примера без моделей и БД.

Проверка перед передачей: [команды для терминала, ручной curl/CLI и ожидаемые ответы](docs/MEDIA_IMPORT_CHECK.md). После ревью полный backend: 652 passed/1 прежний warning; ошибки повреждённых служебных JSON возвращаются безопасно, данные/решения сохранены.

Медиаимпорт U1, 2026-11-07: общий HTTP/CLI принимает проверенные WAV/MP3/PNG/JPEG с preview, UUID-хранением, полным реестровым cleanup и стартовой recovery. На macOS ARM64/Python 3.13.0 получены **652 passed, 1 прежний warning**, pip check и реальные HTTP/CLI/restart; Pillow==13.0.0, mutagen==1.48.1, остальные pins сохранены. Из `backend/`: `../.venv/bin/python -X utf8 -m scripts.check_media_import` (Windows: `..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_import`). Скрипт сам изолирует synthetic DB/uploads/tmp. Текущие [правила и байтовые пределы](docs/IMPORT_HTTP.md), [передача U3/U4](docs/U1_MEDIA_HANDOFF.md), [набор](data/media-import/README.md). Прежние текстовые этапы/цифры ниже исторические. ASR/VL, media-pipeline, binary API и UI просмотра не подключались; Windows и браузер в этом срезе не проверены.

Для media CLI над той же БД сначала остановите сервер и другие CLI: существующий guard действует внутри процесса. Корни uploads/tmp должны быть раздельными, принадлежать одной БД и находиться на одном диске. Preview не оставляет постоянных файлов; при неизвестном результате commit повтор того же набора безопасен. Не удаляйте старые зарегистрированные материалы или owner-маркеры ради восстановления. Политика обработки и SQL v3 не менялись.

Текущий серверный срез 2026-11-07: холодный старт, фильтры тем, PATCH паспорта с версиями/историей и полный текстовый сценарный record/verify на явно authored replay готовы. **587 backend-тестов прошли**, 38 schemas, примеры и pip check корректны. Настоящие ordinary Uvicorn/HTTP/CLI/restart подтвердили сохранность решений, паспортов и данных. Из backend: `..\.venv\Scripts\python.exe -X utf8 -m scripts.check_server_completion` безопасно проверяет новые действия на отдельной synthetic SQLite и 9-шаговый сценарий с 11 точными записями. Контракты/команды/границы — [docs/SERVER_COMPLETION.md](docs/SERVER_COMPLETION.md). Политика обработки v3, старые профили/наборы не переписываются. Live, качество и мультимодальный/UI-показ не объявляются готовыми; прежние этапные цифры ниже исторические.

Серверный этап 2026-11-07: готовы ручные resolve review (attach/create/insufficient_data), полный PATCH бизнес-решений, merge-preview/merge, exclude и Markdown-сводка. PR #9 U3 и #10 U2 уже в main 8f69c7d; в feat/server-text-integration исправлены подключение propose.md, повтор старых new_topic checkpoint и холодный старт. Примеры и pip check корректны. Контракты ручных действий — [docs/HUMAN_WORKFLOW.md](docs/HUMAN_WORKFLOW.md), текущая интеграция/ограничения — [docs/TEAM_INTEGRATION.md](docs/TEAM_INTEGRATION.md). Из backend: `..\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_proposal_binding.py` проверяет cold/warm стык на отдельных synthetic/authored SQLite и точном replay. Модели не вызываются.

Внутренний пилот анализа продуктовой обратной связи. Текущая интеграционная сборка: **SQLite v3**, текстовый импорт HTTP/CLI, TextNormalizer участника 1 и AnalysisService участника 2, API тем/источников/решений/runs, checkpoints и строгий текстовый replay. Обычный `app.main:app` подключает реальные компоненты по утверждённому ProcessingProfile и точному набору записей. Без конфигурации POST /api/v1/runs остаётся 503 INTEGRATION_NOT_READY; live Qwen ещё не подключена, fallback отсутствует. Автоматическое создание темы после полного поиска работает и для пустого каталога, если модель явно подтверждает отсутствие совпадения и возвращает валидный ProposedTopic. Неоднозначность остаётся review.

U1-ветки включены в main (merge e0d75e9, очистка cad1574). Проверенные серверные исправления U3 передаются из основной папки через feat/server-text-integration; merge в main пользователь выполняет после просмотра PR. START_ROLE_1.md не возвращается.

Разработка продолжается в единственной основной рабочей папке GPN_Insight. Прежний var/checkouts/server-integration-20261106 — архив проверки, не второй проект для разработки. Папка var/ игнорируется Git. Push отправляет коммиты указанной ветки; архивы, рабочие БД и .env не отправляются. Перед switch/pull проверяйте git status: сначала сохранить текущие правки. На чистой main обычное обновление — git pull --ff-only origin main, новую задачу начинать в отдельной ветке от неё.

Импорт для участника 4: [docs/IMPORT_HTTP.md](docs/IMPORT_HTTP.md), сборка сервера: [docs/RUNTIME_PROFILE.md](docs/RUNTIME_PROFILE.md). Ручное разрешение review реализовано сервером; дальнейшая политика анализа и cold-start принадлежат U2. Наличие authored-записей не является измерением качества модели.

Новый этап и границы интеграции: [docs/PIPELINE.md](docs/PIPELINE.md). Проверка полного инфраструктурного сценария без живых моделей: из backend/ `..\.venv\Scripts\python.exe -X utf8 -m scripts.check_pipeline` (на macOS `../.venv/bin/python -m scripts.check_pipeline`). Она использует только синтетические адаптеры tests и отдельную БД, проверяет настоящий рестарт и сохранность решений.

Участнику 4: [контракты, JSON-примеры и пошаговая проверка из двух PowerShell-терминалов](docs/API_HANDOFF.md). Обычный запуск ниже не загружает фикстуру; на пустой базе GET /api/v1/topics возвращает `[]`.

Текущий Frontend: [запуск на 5173, проверки и ручная приёмка](frontend/README.md). Подключены список/карточка/источник и PATCH-решение new/needs_research. Сохранение подтверждается отдельным GET со сверкой версии/истории; 409 требует явного принятия актуальной версии, ошибки сохраняют черновик. В слитом PR #8 U4 сообщил о 84 frontend-тестах, lint/build и ручной приёмке. Здесь Node/npm отсутствуют: эти проверки и визуальный прогон не повторялись. UI импорта и разрешения review ещё не реализован; backend-контракты для них переданы отдельно.

Контракт регистрации для участника 1, границы транзакций, миграция и список repository-методов описаны в [docs/STORAGE.md](docs/STORAGE.md). Перед первым init-db/запуском новой версии остановите старый сервер. Для v1/v2 автоматически создаётся SQLite backup в соседнем backups/, затем выполняется атомарный перенос до v3 без сброса данных; повторный init-db безопасен. Общий guard для будущей фиксации импорта — Repository.import_commit(), до размещения файлов и открытия DB-транзакции.

Проверено на Windows, **Python 3.13.0**. Node.js/npm для backend не нужны. Прямые зависимости указаны в `backend/requirements.in`, все фактически установленные версии зафиксированы в `backend/requirements.txt`. Python 3.12 на этой машине не проверялся. `tzdata` нужна для `Europe/Moscow` на Windows.

## Текстовый preview и фиксация участника 1

Сервис `app.ingestion.importer.import_tickets(manifest, assets=(), dry_run=True, repository=...)` выполняет preview, а `dry_run=False` фиксирует текстовый набор. Manifest — UTF-8 JSON (байты или строка) с ровно тремя полями: `dataset_id`, явный boolean `synthetic`, `tickets`. Схемы `TicketInput` и `DatasetRegistration` переиспользуются; `product_id` — только `webneft`. `synthetic=false` не подменяется сервером и само по себе не является разрешением на реальные данные. В `data/demo/manifest.json` четыре явно синтетических текстовых обращения; это ещё не полный мультимодальный demo-набор.

Пределы этого среза: 1 МиБ (1 048 576 байт) manifest, 100 тикетов, 32 уровня вложенности JSON. Повторные ключи, неизвестные поля, неверные даты без часового пояса, некорректные Unicode/числа и ID отвергаются. Вложения в manifest и переданные `assets` пока отклоняются целиком. Проверка останавливается на первой ошибке (`counts.invalid=1`, остальные counts=0); она не выдаёт частичный результат и не обещает полного списка ошибок. На корректном входе `counts` вычисляются по согласованному снимку SQLite, а предупреждение помечает их предварительными.

Для вызова из Python при рабочей папке `backend/`:

```python
from app.config import REPO_ROOT
from app.ingestion.importer import import_tickets
from app.repositories import Repository

preview = import_tickets(
    (REPO_ROOT / "data/demo/manifest.json").read_bytes(),
    dry_run=True,
    repository=Repository(REPO_ROOT / "var/preview-check.db"),
)
print(preview.model_dump_json(indent=2))
```

Это отдельная проверочная БД, не автоматически создаваемая фикстура: подготовьте SQLite v2 заранее отдельной существующей командой `init-db` с соответствующим `APP_DB_PATH`. Сам preview использует `mode=ro`, не создаёт/мигрирует БД, не вызывает регистрацию, модели или FastAPI lifespan. Отсутствие базы даёт `PREVIEW_DATABASE_MISSING`, неподдержанная версия — `PREVIEW_SCHEMA_UNSUPPORTED`, ошибка чтения/схемы — `PREVIEW_DATABASE_UNAVAILABLE`; `can_commit=false`. До нормализации тексты остаются оригинальными, автоматического маскирования в этом срезе нет.

Текстовые HTTP/CLI и commit реализованы U1: [IMPORT_HTTP](docs/IMPORT_HTTP.md). Commit повторяет проверку и возвращает окончательные counts после SQLite commit. `source_content_hash()` используется preview/регистрацией без изменения прежних хешей; подробнее — `docs/STORAGE.md`. Медиа по-прежнему отклоняются целиком.

Preview поддерживает обычный журнал SQLite, используемый проектом по умолчанию. WAL-база отклоняется до открытия SQLite с `PREVIEW_JOURNAL_UNSUPPORTED`: даже `mode=ro` может создать `-wal/-shm`. Автоматически менять режим существующей БД preview не будет. Для проверки используйте отдельную SQLite v2 с обычным журналом. Таблицы/поля источников проверяются и при пустом массиве tickets; неполная схема не даёт успешный preview.

Проверено после ревью на macOS ARM64, Python 3.13.0 с существующим `backend/requirements.txt`: из `backend/` команды `../.venv/bin/python -m pytest -q tests/test_import_preview.py` — 64 passed; полный `../.venv/bin/python -m pytest -q` — 126 passed, одно прежнее предупреждение Starlette/AnyIO; `../.venv/bin/python -m app.cli validate-examples` — 16 общих/7 HTTP-примеров; `pip check` — без нарушений. На Windows используются эквивалентные пути `..\.venv\Scripts\python.exe`. Контрактные JSON и lock-файл не менялись.

### Повторяемая ручная проверка preview

Исправления переносимости Windows для коммита `2be1000` находятся в [portability-fix.patch](portability-fix.patch) и уже применены в текущей рабочей копии: имя временной БД не содержит запрещённый `?` (кириллица, пробелы и `#` сохранены), 17 случаев с произвольными/большими входами имеют короткие pytest ID. После исправления самый длинный ID во всём наборе — 136 символов вместо 3 145 837, число тестов осталось 126. Патч проверен на чистой копии файла из `2be1000`; повторно применять его к уже исправленному файлу не нужно.

Источник проверки Windows: сообщение участника 3 от 2026-11-05. Он сообщил исходные `119 passed, 5 failed, 4 errors` и `126 passed` после только этих двух исправлений в отдельной диагностической копии; preview-сценарий у него прошёл с сохранением файлов, решений и истории. Текущий повторный прогон автором исправлений выполнен на macOS ARM64/Python 3.13.0: `126 passed`, ручной preview, 16/7 примеров и pip check успешны. Самостоятельный запуск исправленной рабочей копии на Windows в этой сессии не выполнялся. Серверный код и контракты этими исправлениями не менялись.

Каждый участник создаёт `.venv` на своей машине по закреплённым зависимостям. Переносить готовое окружение между Windows и macOS нельзя. Из `backend/` в Windows PowerShell:

```powershell
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_import_preview
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -m pip check
```

Из `backend/` на macOS/Linux:

```sh
../.venv/bin/python -m scripts.check_import_preview
../.venv/bin/python -m pytest -q
../.venv/bin/python -m app.cli validate-examples
../.venv/bin/python -m pip check
```

В PowerShell из `backend/` используйте `..\.venv\Scripts\python.exe -X utf8` вместо `../.venv/bin/python` с теми же аргументами. `scripts.check_import_preview` — отдельная проверочная процедура, а не CLI-команда загрузки. Она явно готовит новую синтетическую БД в уникальном `var/preview-review-<uuid>/`, сохраняет тестовое решение и регистрирует подготовленные источники через существующий Repository только для подготовки повторов; рабочие БД и `.env` не используются. Сам `import_tickets` вызывается для preview и проверки отказа `dry_run=False`. Каждый вызов сравнивает файлы до/после и сохранённую карточку. Отчёт создаёт скрипт после проверок, не preview.

Ожидаемый вывод первой команды (фактически получен при ревью):

```text
synthetic=true; mode=preview
new-sources: valid inserted=4 updated=0 unchanged=0 invalid=0
repeat: valid inserted=0 updated=0 unchanged=4 invalid=0
delta: valid inserted=1 updated=1 unchanged=2 invalid=0
duplicate-key: invalid JSON_DUPLICATE_KEY
media: invalid MEDIA_NOT_SUPPORTED
missing-db: invalid PREVIEW_DATABASE_MISSING
write-mode: invalid IMPORT_NOT_IMPLEMENTED
wal: invalid PREVIEW_JOURNAL_UNSUPPORTED
incomplete-db: invalid PREVIEW_DATABASE_UNAVAILABLE
PASS: preview files unchanged; decision/history/count=4 preserved after reopen
report: var/preview-review-<новый uuid>/result.json
```

При повторе скрипт создаёт новый каталог; прежние отчёты и решения не удаляются. Для остальных команд ожидаются `126 passed`, 16 общих/7 HTTP-примеров и `No broken requirements found`. Предупреждение Starlette/AnyIO относится к TestClient и уже описано ниже; длительность тестов и UUID будут отличаться.

## Запуск из PowerShell

Из корня репозитория:

```powershell
python --version
if (-not (Test-Path -LiteralPath .venv/Scripts/python.exe)) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -X utf8 -m app.cli init-db
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Если `.venv` уже создана для этого проекта, повторять `venv` не требуется. Активация окружения и изменение политики PowerShell не нужны. `-X utf8` обеспечивает читаемый русский вывод CLI. Остановка сервера — `Ctrl+C`.

В браузере API проверяются по http://127.0.0.1:8000/api/v1/health или http://127.0.0.1:8000/api/v1/topics. Корень http://127.0.0.1:8000/ пока возвращает 404 с HTTP_ERROR: Backend ещё не отдаёт frontend/dist. Текущая панель открывается отдельно через Vite на http://127.0.0.1:5173/; это не ошибка запуска Backend.

Все пути из `.env` считаются от корня репозитория независимо от рабочей папки. База по умолчанию — `var/insight.db`. `.env`, `var/`, базы и окружение исключены из Git. `init-db` создаёт недостающие таблицы, не сбрасывает данные. `seed-demo` атомарно загружает только известное синтетическое начальное состояние: четыре текстовых источника и одну тему. Точный повтор возвращает `unchanged`, сохраняет решение и историю. Изменённая фикстура или посторонняя непустая база отклоняются; для нового эксперимента задайте отдельный `APP_DB_PATH`, не удаляйте существующую базу.

## Проверка HTTP и сохранения

Для синтетической проверки отдельно выполните процедуру [API_HANDOFF.md](docs/API_HANDOFF.md): задайте новую тестовую APP_DB_PATH, вызовите `seed-demo`, затем запустите сервер. Команды ниже предполагают, что фикстура уже явно загружена в выбранную тестовую базу; они не предназначены для пустой базы обычного запуска.

Во втором PowerShell-терминале, из корня репозитория:

```powershell
$baseUrl = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$baseUrl/health" | ConvertTo-Json -Depth 10
$cards = Invoke-RestMethod "$baseUrl/topics"
$topicId = $cards[0].topic.topic_id
$card = Invoke-RestMethod "$baseUrl/topics/$topicId"
$card | ConvertTo-Json -Depth 20
Invoke-RestMethod "$baseUrl/sources/DEMO-T001" | ConvertTo-Json -Depth 10
$decision = Get-Content -Raw -Encoding UTF8 data/contracts/decision.json | ConvertFrom-Json
$decision.version = $card.topic.version
$body = [System.Text.Encoding]::UTF8.GetBytes(($decision | ConvertTo-Json))
Invoke-RestMethod "$baseUrl/topics/$topicId/decision" -Method Patch -ContentType 'application/json; charset=utf-8' -Body $body
```

После остановки и повторного запуска сервера GET той же карточки показывает `needs_research`, владельца и событие в `history`. Счётчик остаётся 4, `requester_count=null`: идентификаторов заявителей нет. Повтор PATCH со старой `version` даёт `409 VERSION_CONFLICT`; неполное решение — `422 VALIDATION_ERROR` без повторения входных данных в ошибке. `version` должна быть целым JSON-числом: строки, bool и дробные значения отвергаются с 422. Решение и история сохраняются одной транзакцией; ошибка записи истории откатывает и изменение темы.

Доступные ручки:

| Метод и путь | Реализованный результат |
|---|---|
| `GET /api/v1/health` | Состояние сервера/SQLite отдельно от моделей |
| `POST /api/v1/runs` | 202 с Run при подключённых компонентах; сейчас 503 INTEGRATION_NOT_READY, без создания фиктивного запуска |
| `GET /api/v1/runs/{run_id}` | Сохранённое состояние, origins, errors и прогресс; неизвестный ID — 404 |
| `GET /api/v1/topics` | Сохранённые карточки; фильтр `support_level=single\|repeated`, по умолчанию repeated; пустая база — `[]` |
| `GET /api/v1/topics/{topic_id}` | Тема, программные счётчики, источники и история |
| `GET /api/v1/sources/{source_id}` | Сохранённый источник, текущая версия, фрагменты/потребности; document может быть null |
| `PATCH /api/v1/topics/{topic_id}/decision` | `new` или `needs_research`, с `owner`, `reason`, ожидаемой `version` |
| `GET /openapi.json` | Локальная OpenAPI-схема реализованного HTTP API, включая ошибки |

Другие корректно заполненные бизнес-решения пока дают `501 FEATURE_NOT_IMPLEMENTED`; отсутствующие обязательные поля — `422`. Импорт, запуски, разрешение review, merge, exclude и отчёт пока не подключены. Неподдерживаемых ручек нет, они возвращают `404`. Период и остальные фильтры списка ещё не реализованы; их передача даёт `422 UNSUPPORTED_FILTER`, а не молчаливую выдачу без фильтра. Swagger/ReDoc отключены, поскольку стандартные страницы загружают ресурсы с внешнего CDN. В работающем приложении внешних запросов нет.

## Русский текст и существующая демо-база

Исправлены две причины искажений: ранние синтетические JSON были созданы через PowerShell-конвейер с потерей кириллицы, а Windows PowerShell 5.1 неверно декодировал JSON-ответ без явно указанной кодировки. Теперь три файла `data/contracts/*.json` записаны в UTF-8, fixture_id — `foundation-text-utf8-v2`, сервер возвращает `application/json; charset=utf-8`. Добавлена проверка русского текста в файлах, HTTP и SQLite; отдельно прошёл настоящий Invoke-RestMethod Windows PowerShell, включая сохранение и повторное чтение после рестарта.

Если старая база уже содержит `????`, смена кодировки терминала их не восстановит: там сохранены сами знаки вопроса. История старой базы не переписывается. Исправленное начальное состояние имеет новые хеши и производные ID, поэтому существующая база не перезаполняется командой seed-demo. Для проверки исправленного набора используйте отдельную БД, сохранив старые решения:

```powershell
# В терминале сервера сначала остановить Uvicorn через Ctrl+C.
Set-Location C:\Users\user\projects\GPN_Insight\backend
$env:APP_DB_PATH = 'var/utf8-demo.db'
..\.venv\Scripts\python.exe -X utf8 -m app.cli seed-demo
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Этот выбор действует для текущего терминала и его серверного процесса; `.env` и `var/insight.db` не меняются. Затем заново выполните команды второго терминала выше: они получают актуальный topic_id и version. Русские owner/reason должны читаться как «Тестовый владелец» и «Проверить источники синтетической карточки.». Ответ в Windows PowerShell и запись в SQLite проверены на точное совпадение этих строк.

Сообщение Git `nothing to commit, working tree clean` означает, что отслеживаемые файлы совпадают с коммитом. Это нормальное состояние после коммита `dbf7149`; локальные решения в игнорируемом `var/` не изменяют Git status.

## Режим и модели

`APP_MODEL_MODE=replay` — выбранная конфигурация, **не обещание готовности pipeline**. Строгий текстовый replay реализован: без APP_RECORDINGS_ROOT/APP_REPLAY_MODEL_ID/APP_REPLAY_NORMALIZATION_HASH он сообщает REPLAY_NOT_CONFIGURED, настроенный набор — REPLAY_READY только для точных сохранённых запросов. ASR/VL пока REPLAY_NOT_IMPLEMENTED. Health показывает idle/busy отдельно от pipeline_ready и integration_errors. Пока компоненты участников 1/2 не подключены, pipeline_ready=false. Старая seed-карточка имеет synthetic=true, data_origin=authored, analysis_mode=null и не становится модельным результатом после изменения режима.

В `live` без адреса/ID будет `MODEL_NOT_CONFIGURED`. С заполненными адресом/ID — `MODEL_ADAPTER_NOT_IMPLEMENTED`, `checked_at=null`: доступность не проверялась, протокол неизвестен. Токены не попадают в health. Вызов заглушки gateway выбрасывает `GatewayUnavailable` с явным кодом; автоматического переключения режимов и фиктивных успешных ответов нет. Реальные внутренние модели, качество и скорость пока не проверены.

## Контракты и примеры

Общий контракт v1 — `backend/app/schemas.py`. Объекты запрещают неизвестные поля, даты-время требуют часовой пояс. В `data/contracts/examples.json` находятся **16 синтетических образцов форматов**, а не результаты выполненных операций. Аудиоматериал там показывает только форму JSON; файл аудио не поставляется. `demo_state.json` — отдельное вручную подготовленное начальное состояние; оно не заменяет импорт участника 1 и не входит в оценку качества моделей.

Из `backend/`:

```powershell
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -X utf8 -m app.cli export-schemas
..\.venv\Scripts\python.exe -m pytest -q
```

Первая команда валидирует все 16 примеров и ссылки/цитаты тестового состояния, не меняя базу. Для этой текстовой фикстуры каждый подготовленный фрагмент должен дословно входить в исходное обращение; выдуманный текст и ASR/VL-фрагменты без материалов отвергаются. Вторая команда сохраняет самостоятельные JSON Schema в `var/contracts/schemas.json`. Общие схемы результата нормализации и потребностей не подтверждают безопасность файла или истинность распознавания: эти проверки добавляют владельцы сервисов. Итоговый `TopicAssignment` — серверная структура; модель возвращает только `BatchAssignment`, без права объявить `new` и установить полноту поиска.

Тестовые базы создаются в уникальных каталогах `var/pytest/<uuid>/`, отдельно от рабочей БД. Это устраняет наблюдавшийся в пользовательском PowerShell сбой `PermissionError: WinError 5` при доступе к системному `Temp/pytest-of-user`: тогда 4 теста прошли, а 18 завершились ошибкой подготовки временного каталога. Права системного Temp не меняются, чужие временные данные не удаляются. Каталоги тестов остаются в игнорируемом `var/` для диагностики. После исправления все 22 теста проходят и при специально непригодном системном временном каталоге pytest.

## Передача команде

**Участник 1:** `TicketInput`, `MaterialInput`, `Fragment`, `NormalizedDocument`, `MaterialError`, `ImportIssue`, `ImportPreview/Result`, а также новые `DatasetRegistration` и `RegisteredMaterial`. После проверки/перемещения файлов вызвать `Repository.register_dataset`; он атомарно сохраняет только SQLite-записи и завершённый import_log. Общей транзакции с файлами нет. Маскирование, сигнатуры, лимиты, восстановление файлов, `imports_api.py` и реальные ASR/VL остаются участнику 1. Полный реестр для сверки uploads — `registered_materials()`, включая исторические версии. Подробный контракт и порядок очистки — в docs/STORAGE.md; `seed-demo` импорт не заменяет.

**Участник 2:** реализация Analysis.extract_needs/assign_topic с ExtractionResult/AssignmentResult и ProcessingProfile — граница в docs/PIPELINE.md. Pipeline уже обеспечивает два прохода, стабильные fragment/need/topic IDs и транзакционное сохранение назначений. Полный снимок каталога, версии и provenance проверяются. Точный TextGateway — app.gateways.text_gateway(settings); он также доступен как app.state.text_gateway. Промпты, поиск по порциям, группировка и review-политика остаются участнику 2. RecordingGateway позволяет захватить ответы реального gateway и опубликовать набор после успешного сценария; live-адаптер ещё не подключён.

**Участник 4:** [docs/API_HANDOFF.md](docs/API_HANDOFF.md), [docs/PIPELINE.md](docs/PIPELINE.md), `/openapi.json`, RunRequest/Run и data/contracts/run.json. POST /runs возвращает 202 с Run при готовых компонентах, GET /runs/{id} — сохранённый прогресс; сейчас обычный POST даёт 503 INTEGRATION_NOT_READY. Показывать state/errors/origins отдельно от coverage; повтор активного клика использует прежний run_id. Чтение карточек и решения new/needs_research сохранены. Не показывать неподключённый анализ рабочим live-путём.

Последовательный worker, checkpoints и строгий текстовый replay реализованы. Полная обработка без компонентов участников 1/2, live, медиа, полный сценарий record/evaluate и сквозной Frontend остаются интеграцией; ограниченная панель health/runs описана в frontend/README.md. Репозиторий хранит бизнес-решения кроме merged; HTTP сохраняет new/needs_research. Полные пользовательские операции merge/exclude/review не добавлялись.

## Фактически выполненные проверки

Этап pipeline/replay (2026-11-05): **176 passed**, 16 общих/7 HTTP-примеров валидны, export-schemas — 27 типов, pip check успешен. Проверены две стороны import/run guard, активный клик, порядок двух проходов, rollback данных/checkpoint, отказ неизвестного replay и неизменность опубликованного набора. Настоящий проверочный Uvicorn прошёл аварийный рестарт между проходами и reuse 4/6 шагов без потери решения. Копия пользовательской v2 прошла v3/repeat с прежними данными и integrity/FK; рабочая БД не менялась. Полного live-пути пока нет; только tests-адаптеры в scripts.check_pipeline.

Минимальный HTTP-этап (2026-11-04): **62 passed**, 16 общих и 7 HTTP-примеров валидны, pip check успешен. Проверены пустая база без фикстуры, источник до нормализации, new/needs_research, обязательные поля, 422/409 и атомарный откат при ошибке истории. Настоящий Windows PowerShell/Uvicorn подтвердил оба решения и кириллицу после рестартов, count=4; тестовый сервер остановлен. Результаты ниже относятся к предыдущим этапам.

После проверки перед передачей исправлены четыре дефекта: ручное создание темы после исключения теперь сохраняет attach-переопределение с автором/причиной; total_materials нельзя уменьшить; ASR/VL проверяется по kind материала; синтетические свидетельства нельзя представить как реальные. 54 теста проходят, HTTP-путь и сохранение после рестарта повторены на отдельной базе. Финальная проверка пользовательской storage-check.db в режиме чтения подтвердила целостность и два решения с корректной кириллицей (версии 2 и 3). Закрытие терминала сервера не удалило решения. Повторный HTTP-сценарий на отдельной базе подтвердил 409, сохранность после рестарта и ticket_count=4; тестовый сервер остановлен.

Текущий слой SQLite v2: **54 passed**, прежние 16 примеров валидны. Проверены миграция/откат, резервная копия, повторная и конкурентная регистрация, версии/материалы, distinct source_id, сохранность решений, ручные переопределения, review и run. На отдельной SQLite-копии настоящей локальной базы сохранены topics/decision_events/demo_fixtures; рабочая база не менялась. Настоящий Uvicorn прошёл HTTP-путь и рестарт с сохранением решения. Подробности и ограничения — в docs/STORAGE.md.

2026-11-04, после проверки перед передачей: Python 3.13.0, `pip check` — без нарушенных зависимостей; `pytest -q` — **22 passed**; 16 JSON-примеров и тестовая фикстура валидны. Дополнительно проверены строгий тип version, конкурентные сохранения, откат изменения темы при сбое истории, атомарность seed при сбое, связь фрагментов с исходным текстом, отказ неподдерживаемых фильтров и отсутствие тестовых секретов в health/сериализации настроек. В 23 изменённых/новых текстовых файлах признаков ключей и заполненных конфигурационных токенов не обнаружено; единственный токен в тесте — явно синтетический sentinel. `.env`, база и `.venv` исключены из состава изменений.

Есть одно DeprecationWarning при импорте `Starlette 0.46.2 TestClient`: обращение к `anyio.abc.BlockingPortal` в вычисляемом описании типа. В установленной `AnyIO 4.16.0` это устаревший alias того же класса `anyio.from_thread.BlockingPortal`; тождественность классов проверена локально. Это [известная проблема TestClient](https://github.com/Kludex/starlette/issues/3497). В текущем lock сервер работает, тесты проходят, данные и транзакции от предупреждения не зависят. Импорт приложения и настоящий Uvicorn с `-W error::DeprecationWarning` прошли: сервер TestClient не импортирует. При включении warnings-as-errors для самих тестов предупреждение остановит сбор тестов; при будущем удалении alias потребуется согласованное обновление совместимых FastAPI/Starlette/AnyIO. Зависимости и файлы site-packages не менялись, предупреждение не подавлялось.

Отдельно запущен настоящий процесс Uvicorn: HTTP health, карточка, источник, OpenAPI, сохранение решения и повторное чтение после остановки/запуска процесса прошли на отдельной базе в `var/smoke-*/`. После исправлений HTTP-путь повторён с проверками 422 для неверного типа версии/неподдерживаемого фильтра и 409 для старой версии; результат в `var/review-*/result.json`. Повтор seed дал unchanged. Рабочая база при проверках не сбрасывалась. Эти результаты подтверждают только основу backend; интеграция моделей, импорт, полноценный replay и весь пользовательский сценарий пилота не проверены и не объявляются готовыми.

После исправления Windows Temp повторно проверены обычный pytest и pytest с непригодным системным временным корнем — оба раза 22 passed. Проверки примеров, init-db, повторный seed, HTTP-запуск и рестарт Uvicorn с сохранением решения прошли на отдельной базе; результат в `var/terminal-fix-*/result.json`.

После исправления UTF-8: 23 passed, 16 примеров валидны. Новый тест воспроизвёл повреждённые JSON до исправления. Вызов Windows PowerShell до добавления charset воспроизвёл неверную декодировку; после исправления title, owner и reason совпадают с UTF-8-файлами, в том числе после рестарта. Отчёт — `var/utf8-verified-*/result.json`; подготовлена отдельная `var/utf8-demo.db`. Исходная база с историей пользователя не изменялась.
