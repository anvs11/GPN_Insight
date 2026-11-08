# Подготовить серверный стык зарегистрированных медиа

Этот ExecPlan поддерживается по `.agent/PLANS.md` и дополняет основной `.agent/EXECPLAN.md`. Основа — main `6f3b8df` (PR #12 U1, PR #13 U4), ветка `feat/media-runtime-readiness`. Нового клона нет.

## Purpose / Big Picture

После этого среза сервер сможет получить проверенный `ApprovedAsset` для конкретных product/source/version/asset из постоянного хранилища. Это внутренняя подготовка для будущего ASR/VL, а не распознавание и не API выдачи оригиналов. Дополнительно Windows-приёмка перестанет падать на недоступном создании symlink, а ручная текстовая проверка будет использовать новый точный профиль, сохраняя старый пример.

## Progress

- [x] (2026-11-08) Проверены чистый main и два merge; создана отдельная ветка. Установлены только Pillow==13.0.0 и mutagen==1.48.1 с --no-deps; первоначальное окружение их не содержало.
- [x] (2026-11-08) Воспроизведён ANALYSIS_PROFILE_INVALID в scripts.check_text_normalizer на старом handoff v1. Подготовлен снимок private SHA/пакетов/старого JSON в var/reviews/media-runtime-readiness/before.json.
- [x] (2026-11-08) Windows baseline: 4 failed/648 passed; воспроизведены symlink privilege, UTF-8 child decoder и пропущенное staged-content изменение. Реальный symlink skip только WinError 1314; остальные проверки сохранены/разделены, добавлена reparse-проверка. Подмена с прежним mtime теперь сверяется с intake SHA и отвергается.
- [x] (2026-11-08) Read-only resolver и 29 cases: точные source/version/material, исторический файл, owner/path/type/size/hash, неверные/повреждённые контексты и отсутствие создания БД/roots. 28 passed/1 symlink skipped отдельно.
- [x] (2026-11-08) Новый synthetic handoff v2 и регрессия текущего build_text_profile; прежний JSON сохранён побайтно и legacy policy не обновляется автоматически. check_text_normalizer реально прошёл.
- [x] (2026-11-08) Реальные ordinary HTTP/CLI media/restart и resolver/reopen/ошибки прошли; прежний текстовый сценарий также PASS. .env и четыре рабочие SQLite совпали по SHA; packages/pins/legacy сохранены, credential pattern findings=0. Обновлены основной план и docs/MEDIA_RUNTIME.md.
- [x] (2026-11-08, показ/публикация) Подготовлен отдельный synthetic/authored replay demo в var, без изменения .env/рабочей SQLite. Portable Node 24.22.2 получен с официального nodejs.org с проверкой SHA, npm ci сохранил lock; 143 frontend tests и build прошли. Пользователь открыл UI и остановил показ. Автоматическая визуальная проверка заблокирована инструментом, ручное сохранение в браузере нами не проверялось. Пользователь поручил commit/push текущей feature-ветки; main не сливается.
- [ ] ASR/VL/media Normalizer U1, errors/coverage/оценка U2, media profile/replay и binary API после согласования входов. На этой Windows нет права на реальные symlink; четыре link-case требуют среды с привилегией. Публикация не поручена.

## Context and Orientation

U1 реализовал ingestion/media_files.py и security.py: проверку загрузки, регистрацию до короткой SQLite-транзакции, owner marker и recovery. Staged ApprovedAsset недолговечен. StorageRepository.get_source/source_materials читают версии, но не проверяют постоянный файл. gateways.ApprovedAsset содержит asset_id, absolute_path, content_hash, mime_type. U4 добавил фильтры/паспорт; UI не меняется.

## Plan of Work

В backend/app/media_runtime.py добавить функцию resolve_registered_asset(repository, settings, *, source_id, asset_id, source_version, product_id='webneft') -> ApprovedAsset. Версия обязательна: не подменять старый run текущим файлом. Снимок source/material читается одной read-транзакцией. Проверять полный набор assets и повторно вычислять source_content_hash, соответствие kind/MIME/имени, owner каталога, допустимый постоянный storage_path, обычный файл, размер и SHA. Не создавать root, owner, БД или запись; не выполнять recovery/cleanup. Переиспользовать проверки U1, не менять его импорт и не включать resolver в ещё неготовый media pipeline.

Windows без права symlink должен пропускать только реальный symlink-case с явной причиной WinError 1314. Healthy CLI import и hardlink/outside-root продолжают выполняться отдельно. Reparse detection проверяется без специальных прав на синтетическом stat и через checked_path. Не менять системные привилегии.

Старый data/contracts/normalization-handoff.json сохранить побайтно. Добавить normalization-handoff-v2.json с build_text_profile текущих реальных prompt hashes и новой synthetic recording_set_version; скрипт выбирает v2 и заранее сверяет профиль. Существующий normalizer, prompts и replay keys не переписывать.

## Milestones

Сначала завершить воспроизводимый Windows baseline и переносимые тесты. Затем реализовать resolver и проверить четыре формата, историческую версию после delta/reopen, отказ на tamper/foreign-owner/path/link и отсутствие записей. После обновления handoff выполнить реальные изолированные процедуры media import и нормализации; рабочие .env/SQLite не участвуют.

## Concrete Steps

Из backend в PowerShell:

    ..\.venv\Scripts\python.exe -X utf8 -m pytest -q
    ..\.venv\Scripts\python.exe -X utf8 -m scripts.check_text_normalizer
    ..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_handoff
    ..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_import
    ..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
    ..\.venv\Scripts\python.exe -m pip check

На другой ОС использовать ../.venv/bin/python. Точные фактические результаты внести после выполнения; ожидается явный skip только там, где ОС не позволяет настоящий symlink, и PASS без моделей в ручных процедурах.

## Validation and Acceptance

Resolver возвращает правильный постоянный абсолютный путь/hash/MIME для каждого из четырёх форматов и исходной версии после обновления. Ошибки не возвращают ApprovedAsset, не раскрывают путь/содержимое и не меняют БД/файлы/решения. Missing DB не создаётся. Изменённые owner/registry/content/size и неизвестные source/version/asset отвергаются. Повтор и reopen сохраняют результат. Media guard и unavailable health остаются честными.

## Idempotence and Recovery

Проверки создают только новые каталоги var; private SHA сравниваются со снимком. Resolver только читает. Никакой общей транзакции с файлами и защиты от злонамеренного локального процесса между возвратом пути и будущим чтением не обещается: будущий gateway обязан повторно открыть через checked_open и сверить hash перед передачей. Один процесс и запрет параллельного CLI/server над общей БД/roots сохраняются. Commit/push разрешены пользователем после приёмки; merge в main не поручен. Демо/Node/базы/логи var и node_modules/dist не включаются в коммит.

## Interfaces and Dependencies

Shared DTO/SQL/API/import/hash алгоритм/модельные протоколы не меняются. Новых пакетов нет. Приватные проверки owner/root U1 используются в серверном мосте с контрактными тестами. Реальные ASR/VL, media Normalizer/profile/replay и binary-source API остаются следующим совместным срезом после входов U1/U2/U4.

## Surprises & Discoveries

Старый handoff сохраняет policy v1 и не проходит текущую сборку v3, хотя unit-тест нормализатора его принимает: тот проверяет только normalization recipe, а не весь Analysis профиль. Windows baseline также выявил intake integrity, а не только ограничение ОС: прежние stat size/mtime совпадали после изменения содержимого. Детерминированная регрессия с os.utime падала до добавления intake SHA, после прошла. Отдельный сбой subprocess был вызван Windows-кодировкой дочернего CLI, не несовместимостью Pillow/mutagen.

## Decision Log

2026-11-08, U3: ограничить текущий срез runtime-resolution и стабилизацией, не реализовывать за U1 ASR/VL. Причина: импорт уже готов, модели/Normalizer/protocol ещё нет. Сохранить legacy JSON отдельным файлом, а не переписывать исторический пример или молча обновлять профиль при запуске.

## Outcomes & Retrospective

Resolver готов как внутренний серверный интерфейс без снятия media guard. На Windows прошли медиа HTTP/CLI/restart, resolver с delta/исторической версией/повреждением, текстовый normalizer/replay и прежний server-completion сценарий. Полный прогон после основной реализации: 678 passed/4 skipped/1 прежний warning. После трёх дополнительных проверок owner/missing-root/byte-limit финальный общий прогон: **681 passed, 4 skipped, 1 warning in 56.92s**. Skips относятся только к реальным symlink; hardlink, выход за root и reparse attribute выполняются.

Отчёты: var/media-import-check-84dda9df00e442bca65fc6afec65f2e6/result.json, var/media-runtime-check-58443606c2ce4f7496aa296ccbf9ef2c/result.json, var/text-normalizer-check-228ecf09d6c14de5a09be377d9af3f98/result.json, var/server-completion-check-4b90cb3257244a1496b8069db546c494/result.json. Итог сохранности — var/reviews/media-runtime-readiness/preservation.json (5 private files, legacy JSON, все package pins сохранены; credential patterns=0). Зависимость следующего этапа — реальный media Normalizer/ASR/VL и согласованный errors/coverage; не заменять это тестовым успехом.

Редакция 2026-11-08: самостоятельный серверный этап после объединения U1/U4, с сохранением рабочих данных и чужого кода.
