# Итоговая приёмка У3 и проверки У1

Последующее развитие 10.11: [двухстраничный интерфейс мониторинга/отчётов, SQLite-архив и новые команды](MONITORING_WORKSPACE.md). Старый безопасный demo запуск ниже продолжает работать, но открывает уже новую оболочку. Датированные результаты этого документа относятся к предыдущему срезу 91a4b78.

09.11.2026. Основа — main `3503dba` с PR #17/#18; работа — `feat/final-integration-safety`. Проверяется программа на синтетических данных и строгом authored replay. Живые модели не вызывались, платные сервисы для показа не нужны.

## Обезличенность по условию кейса

Ответ 14 в [выжимке вебинара](org_feedback.txt) разрешает считать, что входные обращения и звонки уже обезличены. Создание полной системы обезличивания не является обязательным этапом прототипа. Наша дополнительная маскировка контактов — защитная мера; она не доказывает удаление всех имён, адресов, голосов и данных в пикселях.

Фраза «реальные данные пока не подключать: полное обезличивание не реализовано» была неточной. Фактическое ограничение другое: HTTP-импорт и media replay проверены с `synthetic=true`, корпоративной авторизации нет, разрешённый реальный набор нам не передан. Нельзя ставить synthetic реальным данным ради обхода ограничения. Сервер продолжает закрывать несинтетические оригиналы. Допущение задания не является разрешением загружать или публиковать чужие материалы.

## Проверяемые результаты

| Часть | Поведение |
|---|---|
| У1: импорт | WAV/MP3/PNG/JPEG; preview без записи, commit, repeat, delta, recovery, сохранение решений |
| У1: безопасность | Пределы размеров/длительности/пикселей; пути/имена/ссылки; повреждённые payload; недоверенные ASR/VL-тексты; безопасные ошибки и masked cache/recordings |
| У3: pipeline | Штатный bootstrap; последовательный worker; partial/retry; строгий replay; interrupted после настоящего прерывания процесса |
| У3: целостность | Постоянные темы; distinct source counts; решения/паспорта/история/overrides; 409; SQLite integrity/FK; сохранность после рестарта |
| У3: результат | Категории/приоритеты/unknown; source/material coverage; завершённая неделя Москвы; binary API/ranges |
| Интерфейс | React/jsdom → настоящий HTTP → SQLite → React: импорт, partial/errors, файлы, summary/week, retry/решение |

Новые [INJ_AUDIO/INJ_IMAGE](../data/security/synthetic-media-safety.json) — инструкции в **подготовленном тексте** ASR/VL. Проверки запрещают сетевые вызовы, запуск команд и чтение контрольного файла, сверяют quotes и отсутствие контакта в обработанных результатах. Это не adversarial звук/пиксели и не оценка защищённости живой модели. Четыре Windows symlink-проверки пропущены из-за WinError 1314; hardlink/path проверки выполняются. Промышленная security-приёмка не заявляется завершённой.

В просмотре каждого источника добавлен «Возраст обращения»: время от `created_at`, обновляемое раз в минуту по часам устройства. Будущая дата получает предупреждение. Возраст не сохраняется в SQLite и не меняет source hash/version. Без upstream-статуса закрытия это не длительность открытого тикета и не нарушение SLA.

## Команды проверки

В PowerShell из корня:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_media_import
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_partial_weekly_integration
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_frontend_media_http
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_text_pipeline
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_server_completion
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_human_workflow
```

Ожидаются PASS и отдельные `var/*/result.json`. Скрипты используют свои новые SQLite/uploads и свободные localhost-порты, останавливают собственные процессы. Рабочую базу и `.env` не сбрасывают. Слово unavailable в check_media_import относится к намеренно неподключённому профилю **этой** проверки; обычный media bootstrap проверяется check_partial_weekly_integration.

```powershell
Set-Location ..\frontend
$taskNodePath = (Get-Content -Encoding UTF8 ..\var\tooling\node\active-path.txt -Raw).Trim()
$env:PATH = "$taskNodePath;$env:PATH"
& "$taskNodePath\npm.cmd" run test -- --run
& "$taskNodePath\npm.cmd" run build
& "$taskNodePath\npm.cmd" run lint
```

При Node в обычном PATH достаточно `npm`. Linux/macOS используют `../.venv/bin/python` и обычный npm.

## Стабильный показ

Первый терминал из корня проекта:

```powershell
Set-Location backend
$demoJson = ..\.venv\Scripts\python.exe -X utf8 -m scripts.prepare_acceptance_demo
if ($LASTEXITCODE -ne 0) { throw 'Демо не подготовлено; сервер не запускаем.' }
$demo = $demoJson | ConvertFrom-Json
$demo.selected_files
$demo.text_manifest
foreach ($entry in $demo.environment.PSObject.Properties) {
    Set-Item -Path "Env:$($entry.Name)" -Value $entry.Value
}
..\.venv\Scripts\python.exe -X utf8 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Второй терминал из `frontend/`: `npm run dev` (portable Node — как выше). Открыть http://127.0.0.1:5173. Если порты заняты вашим предыдущим показом, сначала остановить его Ctrl+C в соответствующих терминалах.

Подготовка явно импортирует пять синтетических обращений и запускает их на обычном строгом replay. Runtime API не подставляет карточки. Ответы написаны командой; это не benchmark.

1. По умолчанию видна одна повторяющаяся тема счёта, ticket_count=2. Выбрать «Все темы» — видны три: контакт, ошибка сохранения счёта, экспорт CSV. Открыть источник: дата, возраст, цитаты и оценки. Четвёртый текстовый случай «сделать как раньше» остаётся в review с unclassified/unknown и входит в аналитику без обязательной ручной эскалации.
2. Загрузить `text-manifest.json` в текстовом импорте: preview/commit дают unchanged=4. Для media выбрать `selected-files/manifest.json` и четыре media-файла: unchanged=1. JSON не выбирать как вложение. Повтор не увеличивает версии/счётчики.
3. В теме контакта открыть SYNTHETIC-MEDIA-1: partial 4/5, ошибка PNG, unknown/authored. Последний сохранённый запуск имеет общее material coverage 8/9 (ещё четыре успешных текста). PNG уже восстановлен для retry; старый анализ не меняется задним числом. Просмотр файлов включается отдельными кнопками. Аудио искусственно тихое, речи нет.
4. Без изменения текстов/паспортов запустить обработку без дат: completed, material coverage 9/9, source coverage 5/5. Обновить карточку/источник: ID, owner/status/история сохраняются. Повтор использует успешные этапы. Произвольный новый текст/файл может законно дать REPLAY_RECORD_MISSING.
5. Загрузить review и сформировать/скачать недельный отчёт. На дату приёмки 09.11.2026 период 02–08.11 содержит пять источников, категории, unknown/покрытие. Даты **фиксированы в фикстуре**; показ в другую неделю закономерно даст другой период/числа.
6. Сохранить решение, остановить backend Ctrl+C и снова выполнить последнюю uvicorn-команду в том же первом терминале. После обновления UI данные остаются. Новый запуск подготовки создаёт новое начальное partial состояние в другой базе; старую не удаляет.

Остановка — Ctrl+C в обоих терминалах. После закрытия первого терминала его demo-переменные исчезают; `.env` не менялся. Authored generators используются только процедурами подготовки; штатный сервер читает готовые recordings.

## Фактические результаты и ограничения

Итоговый прогон: **758 backend passed, 4 skipped, 1 warning; 332 frontend passed**. TypeScript/build/lint, pip check, восемь partial/media contract examples и четыре media handoff examples прошли. Warning — прежний deprecated alias BlockingPortal в Starlette; конфликтов зависимостей pip check не обнаружил. Pins не обновлялись.

Результаты отдельных настоящих HTTP/CLI/process проверок сохранены локально:

| Процедура | Отчёт в var/ |
|---|---|
| Медиаимпорт/repeat/delta/restart | media-import-check-f9aeeb7e075447ae88224fabdeab4dd5/result.json |
| Partial/interrupt/restart/409 | partial-weekly-check-d81ff2cfd98248e4a190351ae9d45a2a/result.json |
| React → HTTP → SQLite, 4 действия | frontend-media-http-7818c7c66a9549989a3b2772ee8ddafb/result.json |
| Text pipeline/repeat/unknown delta | text-pipeline-check-618d9797f6de408b9e9a42d96a6006c4/result.json |
| Фильтры/паспорт/scenario record/verify | server-completion-check-649d3b53a29d4a74b1772ae758cd3530/result.json |
| Review/create/merge/overrides/restart | human-workflow-check-769cd07248d143668ad3bfd991343ce2/result.json |
| Новый разнообразный demo, обычный HTTP/restart | acceptance-demo-0b5f5c3b94914dcb84a2048955f80d80/http-acceptance.json |
| Сохранность данных/diff/secrets | reviews/final-integration-20261109/result.json |

Новые 15 backend tests включают 14 проверок media safety и полный regression разнообразного демо. Три frontend tests проверяют длительность, будущую дату и обновление/очистку таймера. Хеши пяти private файлов (`.env` и четыре рабочие SQLite) сохранены; backend runtime/prompts/общие DTO/importer/source hash/pins и исходный org_feedback.txt не менялись. Существенных дефектов существующего runtime при проверках не выявлено. Все собственные HTTP-процессы остановлены; пользовательские процессы не затрагивались. Новые изменения в этом этапе не коммитились и не публиковались.

Визуальный маршрут инструментом не подтверждён: Computer Use вернул `The browser access host is unavailable`. Предыдущие снимки пользователя показывают открывающийся интерфейс; они не заменяют весь маршрут. Редизайн ожидает референсы.

Независимая оценка У2, live при получении протоколов, промышленный доступ и конкурсные материалы остаются отдельной работой. Серверный synthetic/replay сценарий не требует ждать их для демонстрации.
