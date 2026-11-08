# Как проверить этап media import U1

Проверено 7 ноября 2026 года на macOS ARM64/Python 3.13.0: 652 backend-теста, 65 профильных, pip check и реальные ручные curl/CLI/restart. Примеры ниже используют только отдельную синтетическую БД; существующая insight.db не затрагивается. Commit/push для проверки не нужны. Это backend-этап, формы импорта и распознавания в UI пока нет.

## Быстрая проверка одним терминалом

Из текущего проекта:

```sh
cd "/Users/artem/Desktop/GPN_Insight/backend"
../.venv/bin/python --version
../.venv/bin/python -m pip check
../.venv/bin/python -X utf8 -m pytest -q
../.venv/bin/python -X utf8 -m app.cli validate-examples
../.venv/bin/python -X utf8 -m scripts.check_media_import
```

Ожидаемые ключевые строки (время, UUID и report-path будут другими):

```text
Python 3.13.0
No broken requirements found.
652 passed, 1 warning
Примеры: 16; HTTP-примеры: 7; ProposedTopic: valid; workflow: 14; ...
synthetic=true; server=ordinary app.main:app; formats=WAV/MP3/PNG/JPEG; models_called=false
preview: inserted=4; SQLite/import_log/uploads unchanged; temporary files removed
commit/repeat/delta: inserted4 -> unchanged4 -> updated1/unchanged3; old registered paths retained
decision: topic ID/owner/history retained; obsolete need not counted for changed source
PASS: actual HTTP/CLI/restart; no models; media pipeline remains explicitly unavailable
report: var/media-import-check-<новый UUID>/result.json
```

Один известный warning относится к Starlette/AnyIO, не к файлам или модели. В ограниченной среде pip может дополнительно сообщить о недоступном cache; результат совместимости — строка No broken requirements found. Скрипт создаёт собственные SQLite/uploads/tmp, запускает и останавливает собственный localhost-сервер. Его нельзя запускать в окружении, запрещающем localhost sockets, без разрешения среды. Рабочие БД и серверы других задач он не останавливает.

Для отдельного прогона аварийных границ:

```sh
../.venv/bin/python -X utf8 -m pytest -q tests/test_upload_security.py tests/test_media_import.py tests/test_import_recovery.py
```

Ожидается `65 passed, 1 warning`. Внутри есть настоящие остановки дочерних процессов до/после commit; это не только подмена исключений.

## Ручной HTTP-путь, терминал 1

Выберите свободный порт 8011; если он занят, используйте другой в обоих терминалах, не останавливая чужой процесс. Следующий блок создаёт новую папку в var и отдельную БД при каждом запуске:

```sh
cd "/Users/artem/Desktop/GPN_Insight"
export GPN_MEDIA_CHECK_DIR="$(mktemp -d "$PWD/var/manual-media.XXXXXX")"
export APP_DB_PATH="$GPN_MEDIA_CHECK_DIR/check.db"
export APP_UPLOAD_ROOT="$GPN_MEDIA_CHECK_DIR/uploads"
export APP_TMP_ROOT="$GPN_MEDIA_CHECK_DIR/tmp"
export APP_MODEL_MODE=replay
export APP_REPLAY_MODEL_ID=
export APP_REPLAY_NORMALIZATION_HASH=
export APP_RECORDINGS_ROOT="$GPN_MEDIA_CHECK_DIR/no-recordings"
export APP_PROCESSING_PROFILE_PATH="$GPN_MEDIA_CHECK_DIR/no-profile.json"
export APP_MAX_AUDIO_SECONDS=60
export APP_MAX_IMAGE_PIXELS=25000000
cd backend
../.venv/bin/python -m app.cli init-db
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --workers 1 --no-access-log
```

Ожидаются `SQLite v3 готова. Данные и решения не сбрасывались.`, затем `Application startup complete` и `Uvicorn running on http://127.0.0.1:8011`. Терминал остаётся занят сервером. Этот профиль намеренно не подключает модели, поэтому pipeline_ready=false нормально для проверки импорта.

## Ручной HTTP-путь, терминал 2

```sh
cd "/Users/artem/Desktop/GPN_Insight"
curl --silent --show-error http://127.0.0.1:8011/api/v1/health

post_media() {
  curl --silent --show-error -w '\nHTTP %{http_code}\n' \
    -F 'manifest=@data/media-import/manifest.json;type=application/json' \
    -F 'files=@data/media-import/call.wav;type=audio/wav' \
    -F 'files=@data/media-import/call.mp3;type=audio/mpeg' \
    -F 'files=@data/media-import/screen.png;type=image/png' \
    -F 'files=@data/media-import/photo.jpg;type=image/jpeg' \
    "http://127.0.0.1:8011/api/v1/imports$1"
}

post_media '?dry_run=true'
post_media ''
post_media ''
curl --silent --show-error http://127.0.0.1:8011/api/v1/imports
curl --silent --show-error http://127.0.0.1:8011/api/v1/sources/SYNTHETIC-MEDIA-1
```

В health ожидаются `model_mode=replay`, `pipeline_ready=false`; live/replay модели для этого сценария не настроены. В ответах на три последовательных POST смотрите:

| Запрос | Ожидаемый результат |
|---|---|
| Preview | HTTP 200, status=valid, can_commit=true, counts inserted=4/updated=0/unchanged=0/invalid=0 |
| Первый commit | HTTP 200, counts inserted=4/updated=0/unchanged=0/invalid=0 и imported_at |
| Повтор | HTTP 200, counts inserted=0/updated=0/unchanged=4/invalid=0 |
| GET imports | Два завершённых импорта, разные registration_id; это журнал запросов, не дубли источников |
| GET source | source_version=1, synthetic=true, data_origin=imported, assets содержит audio; document=null, requires_analysis=true |

Для безопасного отказа отправьте тот же manifest без JPEG:

```sh
curl --silent --show-error -w '\nHTTP %{http_code}\n' \
  -F 'manifest=@data/media-import/manifest.json' \
  -F 'files=@data/media-import/call.wav' \
  -F 'files=@data/media-import/call.mp3' \
  -F 'files=@data/media-import/screen.png' \
  http://127.0.0.1:8011/api/v1/imports
```

Ожидается HTTP 422 с `code=MEDIA_FILES_MISMATCH`. Запрос ничего частично не импортирует; GET imports по-прежнему содержит два завершённых запроса. Для повторной проверки не редактируйте исходные fixture-файлы в data; сценарий delta уже проверяется изолированно scripts.check_media_import.

## Рестарт и CLI

В терминале 1 нажмите Ctrl+C и дождитесь `Application shutdown complete`. В этом же терминале сохранены APP_* переменные выбранной отдельной БД. Пока сервер остановлен:

```sh
../.venv/bin/python -m app.cli import --manifest ../data/media-import/manifest.json --dry-run
../.venv/bin/python -m app.cli import --manifest ../data/media-import/manifest.json
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --workers 1 --no-access-log
```

CLI preview и commit возвращают `counts unchanged=4, inserted=0, updated=0, invalid=0`; commit добавляет ещё одну завершённую запись журнала. После нового startup в терминале 2 повторите GET source: версия остаётся 1, вложения доступны как метаданные. Старые файлы не удалены. Наконец остановите собственный сервер Ctrl+C. Server и media CLI над одной БД/roots не работают одновременно — guard действует внутри процесса.

Сохранность решения при обновлении bytes и обработку повреждённого owner мы отдельно проверили ручными curl на копии набора: 422 при недостающем файле, 503 IMPORT_STORAGE_OWNER_INVALID при повреждении служебного JSON без изменения SQLite, delta updated=1/unchanged=3, затем source_version=2 и прежние owner/decision/history после рестарта. Отчёт — `var/reviews/u1-media-prehandoff-20261107/manual/result.json`. Для пользователя этот расширенный повторяемый сценарий покрывает scripts.check_media_import, а порчу служебных файлов вручную делать не требуется.

## Границы проверки

Импорт не подключает ASR/VL, media-pipeline, binary API или UI. Тестовые WAV/MP3 — тишина, images — одноцветные поля; качество моделей этим не измеряется. Windows не запускалась. Состояния после порчи owner/реестра/журнала требуют проверки, автоматический repair чужих/legacy данных не выполняется. Полный контракт и ограничения — [IMPORT_HTTP.md](IMPORT_HTTP.md).
