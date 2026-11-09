# Серверная подготовка постоянных медиа

Продолжение 09.11 A4–A7: resolver используется в штатном partial/media Pipeline и synthetic binary API. SQL publication/retry/история, оценки и completed-week report реализованы: [PARTIAL_WEEKLY_HANDOFF.md](PARTIAL_WEEKLY_HANDOFF.md). Нижние ограничения «не подключён» относятся к предыдущему срезу; legacy text/live правила сохранены.

Срез A1–A3 09.11 реализует отдельные partial contracts/Normalizer/strict media replay и безопасность выдачи originals: [PARTIAL_MEDIA_FOUNDATION.md](PARTIAL_MEDIA_FOUNDATION.md). Resolver ниже переиспользован без смены source/hash/import. Production media Pipeline/SQL revision publication пока не подключены (A4).

Обновление 09.11: У4 text import/review уже интегрирован из main `620b741`. Пользователь утвердил категории/приоритеты, анализ успешных частей при ошибке вложения и отчёт за завершённую неделю MSK. Следующая реализация У3/разрешённой части У1 — [NEXT_IMPLEMENTATION_TASKS.md](NEXT_IMPLEMENTATION_TASKS.md); partial ещё не включён в runtime. Прежние требования ждать live endpoints и сохранять полный отказ как будущую политику ниже исторические: доступ не предполагается, а новая обработка требует явных revisions/coverage, не удаления errors.

Срез U3 от 08.11.2026 поверх main `6f3b8df`: импорт U1 и фильтры/редактор паспорта U4 уже объединены. Здесь добавлен внутренний resolver файлов, исправлены Windows-проверки и обновлена ручная текстовая фикстура. ASR/VL, media Normalizer/профиль/replay и API выдачи оригиналов этим срезом не реализованы.

## Интерфейс для участника 1

```python
from app.media_runtime import resolve_registered_asset

asset = resolve_registered_asset(
    repository, settings,
    product_id="webneft", source_id=source_id,
    source_version=source_version, asset_id=asset_id,
)
# asset: ApprovedAsset(asset_id, absolute_path, content_hash, mime_type)
```

`source_version` обязателен и является положительным strict integer. Брать его из входа запуска, не заменять текущей версией. Resolver читает source и зарегистрированные материалы этой версии одним SQLite read snapshot. Он проверяет полный состав assets, kind/MIME и исходный source_content_hash; затем owner хранилища, постоянный UUID-путь U1, тип файла/отсутствие links, размер и точный SHA-256. Путь/имя/URL из пользовательского запроса не становится путём чтения. Source URL не скачивается. Историческая версия продолжает указывать на исторический файл после delta/reopen.

Функция не создаёт БД, каталог, owner, ApprovedAsset для staging или результаты анализа; не делает cleanup/recovery и не вызывает gateway. Чтение ограничено зарегистрированным размером и потолком формата (аудио 10 МиБ, изображение 5 МиБ). Используется тот же owner/path-протокол U1, без нового реестра/SQL/формата импорта. Исходное имя не является идентификатором: повтор с переименованием того же материала не меняет алгоритм source_content_hash.

Безопасные ошибки `RepositoryError`: `ASSET_CONTEXT_INVALID` (422), `SOURCE_NOT_FOUND` и `SOURCE_ASSET_NOT_FOUND` (404), `ASSET_REGISTRY_UNAVAILABLE`, `ASSET_REGISTRY_CONFLICT`, `ASSET_STORAGE_INVALID`, `REGISTERED_MATERIAL_INTEGRITY_FAILED` (503). Сохраняются прежние коды `PREVIEW_*`, `IMPORT_STORAGE_OWNER_*`, `FILE_*` проверок U1. Тексты не раскрывают абсолютный путь и содержимое. Любая ошибка означает отсутствие подготовленного asset, а не успешное распознавание пустого текста.

Возвращается путь, а не удерживаемый файловый дескриптор. Между возвратом и будущей отправкой модели локальный процесс может изменить файл. Поэтому будущий gateway должен использовать `security.checked_open` относительно разрешённого upload root, сверить тот же hash при чтении и не отправлять непроверенные bytes. Этот срез не обещает защиту от локального администратора или общую SQLite-транзакцию с файлами. Один процесс/один worker и запрет одновременного CLI/server над общей БД/roots сохраняются.

## Исправления приёмки Windows

Прежняя проверка staged upload сравнивала только stat fingerprint. Она могла пропустить изменение содержимого при прежних размере/mtime. Теперь workspace считает SHA во время приёма bytes и сверяет его при complete-check до парсинга и перед фиксацией. Регрессия принудительно сохраняет размер/mtime, поэтому не зависит от скорости диска и ОС. Контракт импорта не изменён.

Дочерний CLI в тесте явно использует UTF-8. Symlink-case пропускается только при настоящем Windows WinError 1314, с видимой причиной; остальные ошибки создания links не подавляются. CLI preview/commit/repeat и hardlink/outside-root отделены от symlink-case и выполняются без этой привилегии. Добавлена проверка Windows reparse attribute через синтетический stat и настоящий checked_path; она не подменяет прогон настоящего symlink. Права ОС/Developer Mode не менялись. На этой машине четыре настоящих symlink проверки остаются непроверенными.

Старый `data/contracts/normalization-handoff.json` сохранён побайтно, его policy v1 не подходит общей сборке v3. Новый `normalization-handoff-v2.json` использует реальные три prompt hashes, текущий build_text_profile и отдельную synthetic recording_set_version. `scripts.check_text_normalizer` проверяет v2 без изменения профиля при запуске; старый профиль по-прежнему явно отклоняется сборкой. Это authored replay, не измерение качества модели.

## Проверка из PowerShell

Из корня проекта:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q -rs
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_runtime
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_import
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_text_normalizer
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_handoff
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_server_completion
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -m pip check
```

Для новой среды сначала установить закреплённый `backend/requirements.txt` в `.venv` по README. В уже подготовленной среде этого прогона добавлены только Pillow==13.0.0 и mutagen==1.48.1 с `--no-deps`; остальные версии не обновлялись. На Linux/macOS путь Python — `../.venv/bin/python`.

Ручные процедуры используют только новые synthetic каталоги `var/…`, не `.env`/рабочую SQLite. `check_media_import` запускает собственный ordinary Uvicorn, проверяет HTTP preview/commit/repeat/delta/решение, реальный restart и CLI, затем закрывает свои процессы. `check_media_runtime` проверяет четыре формата, v1/v2, reopen, сохранение решения, отказ на подмену/чужой asset. Ожидаются строки:

```text
resolve: 4 registered files; exact source/version/asset/hash; unchanged repeat
delta/reopen: v1 and v2 resolve distinct persistent files; decision/history retained
PASS: exact versions, tamper/foreign rejection, reopen and private data preserved; media analysis unavailable
```

Фактически на Windows/Python 3.13.0: **681 passed, 4 skipped, 1 warning** (56.92s). Skips — только реальный symlink/WinError 1314, warning — прежний Starlette/AnyIO. Ручные HTTP/CLI/restart, resolver, normalization, handoff, примеры 16/7 + ProposedTopic/14workflow и pip check прошли. Отчёты:

- `var/media-import-check-84dda9df00e442bca65fc6afec65f2e6/result.json`;
- `var/media-runtime-check-58443606c2ce4f7496aa296ccbf9ef2c/result.json`;
- `var/text-normalizer-check-228ecf09d6c14de5a09be377d9af3f98/result.json`;
- `var/server-completion-check-4b90cb3257244a1496b8069db546c494/result.json`.

## Передача и граница следующего этапа

U1: U3 проверил импорт/recovery/health/CLI на Windows и принимает текущие byte-единицы, owner/UUID-хранение, один процесс и точные pins. Подготовка постоянного ApprovedAsset доступна через функцию выше. Следом нужны ASR/VL и media Normalizer: реальные limits/protocol, masking и явные ошибки. При подготовке стыка использовать текущий консервативный отказ анализа всего документа при MaterialError; частичное покрытие отдельно согласовать с U2/U3, не снимать guard молча.

U2: вход остаётся NormalizedDocument с masked fragments и точными source/version/asset. Алгоритмы/промпты/DTO не менялись. Нового media ProcessingProfile или RecordingMeta здесь нет; протокол request hashes/replay готовится при наличии реального Normalizer/выхода gateway. Нужна договорённость об errors/coverage и независимая разметка для качества.

U4: фильтры/редактор паспорта уже в main; новые UI-функции этой задачей не добавлялись и браузером здесь не проверялись. Форма импорта/review и просмотр медиа остаются вашими следующими задачами; внутренний ApprovedAsset не является браузерным URL. API чтения оригиналов не добавлялся.

Обновление 09.11 (A2): HTTP source/card/list/decision/passport/merge-preview теперь используют серверную проекцию originals: nonsynthetic ticket.text/requester_id/имя и URL файла скрываются, original_access=restricted. Raw SQLite не меняется. Производные фрагменты/комментарии не гарантированно обезличены; корпоративная авторизация и полная security-приёмка остаются открытыми. A3 добавляет отдельный строгий media replay, но production Pipeline и live ещё не подключены. Подробности и проверки — в PARTIAL_MEDIA_FOUNDATION.md.
