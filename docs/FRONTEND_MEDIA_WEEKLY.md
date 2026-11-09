# У4: медиа, покрытие, оценки и недельная сводка

09.11.2026. По согласованию пользователя с У4 реализация выполнена в `feat/frontend-media-weekly` от опубликованного серверного ac3ae94. Серверный коммит уже отправлен в `origin/feat/weekly-partial-media`; merge делает пользователь. Клона/второго проекта нет. Новые UI изменения — отдельная рабочая ветка, не смешиваются с первым merge.

## Что подключено

Текстовый ImportPanel сохранён. Новая отдельная форма «Импорт обращений с медиа» принимает UTF-8 manifest и выбранные WAV/MP3/PNG/JPEG. Отправляются исходный JSON и точные File snapshots в multipart manifest/files. Сервер по-прежнему проверяет duplicate keys, пути, signatures/metadata/hashes; браузер не пересобирает JSON. Смена JSON/любого файла отменяет preview, поздний ответ не даёт разрешение на commit. Во время commit поля/повторное нажатие блокируются ref, не только очередным render. Успех — только валидный ImportResult. Потеря/неопределённый ответ блокирует повтор до явной сверки журнала/источников, без автоматического повторного commit. Прежняя квитанция не связывается с новым неуспешным набором.

SourceViewer показывает сохранённое покрытие новой ревизии, partial/empty/failed, pending/manual recheck, category/priority/unknown, основания/неизвестности/origin. Прочитанная source/version/hash/revision связывается с processing ответом; при расхождении требуется обновить источник. В карточке темы отдельная кнопка запрашивает summary той же topic.version, counts — distinct source_id на строку. Категории/приоритеты могут пересекаться; missing не превращается в normal. Старый источник без новой ревизии не становится «без потребностей».

Медиа загружается только после явного клика и только через API IDs/version. Никогда не используется manifest path/source_url. Ответ должен быть HTTP200 с ожидаемым MIME и фактическим bounded размером (5 МиБ image, 10 МиБ audio). Заголовок Content-Length не заменяет подсчёт bytes. Object URL создаётся из полученного Blob, отзывается при замене/закрытии; native image/audio без autoplay. Несинтетический оригинал не запрашивается, 403/404/503 видимы. Ошибка воспроизведения не считается ошибкой/успехом ASR. Для проигрывания уже загруженного Blob ranges сети не нужны; серверный audio range API сохранён.

В панели запуска material/source coverage подписаны отдельно. Показатели проверяются по units/counts; подготовка 100% не обещает успешный анализ. Partial и ошибки сохраняются. Новые read API находятся только в api.ts, модели из браузера не вызываются, runtime fixtures не импортируются.

Недельная панель по кнопке читает серверный период и отчёт, проверяет совпадение недели. Это завершённая MSK неделя, не browser-local последние семь дней. UTF-8 Markdown показывается plain text без HTML/выполнения scripts. Скачивание — полученный Blob, а не непроверенный href. При сбое обновления прежний текст остаётся с явной stale подписью, скачивание нового отчёта не подтверждается. Scheduler/рассылка не заявлены.

## Быстрая проверка без браузера

Из frontend при доступном Node (обычный PATH либо ранее подготовленный portable runtime):

```powershell
npm run test -- --run
npm run lint
npm run build
```

Обычные tests не запускают сетевые модели/сервер и не добавляют skips для отсутствующего backend. Отдельная приёмка с настоящим backend из backend:

```powershell
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_frontend_media_http
```

Процедура сама создаёт synthetic SQLite/uploads/recordings, запускает обычный app.main:app на свободном localhost порту и четыре React/jsdom действия против реального HTTP: preview/commit/repeat, SourceViewer partial/unknown и media, topic summary/weekly, retry с сохранённым решением. Test-only мост переводит jsdom FormData в multipart для Node fetch; production использует стандартный браузерный FormData. Это функциональная HTTP приёмка, не проверка пикселей или слышимости браузерного аудио. Все свои процессы останавливаются, рабочие .env/БД не меняются. Итог — PASS и var/frontend-media-http-UUID/result.json.

## Посмотреть самостоятельно: два терминала

Сначала остановить свой прежний показ Ctrl+C, если он занимает 8000/5173. Не удалять базы и не перезаписывать .env. Первый PowerShell из корня:

```powershell
Set-Location backend
$demoJson = ..\.venv\Scripts\python.exe -X utf8 -m scripts.prepare_frontend_media_demo
if ($LASTEXITCODE -ne 0) { throw 'Подготовка синтетического показа не завершена.' }
$demo = $demoJson | ConvertFrom-Json
$demo.folder
$demo.selected_files
foreach ($entry in $demo.environment.PSObject.Properties) {
    Set-Item -Path "Env:$($entry.Name)" -Value $entry.Value
}
..\.venv\Scripts\python.exe -X utf8 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Подготавливается новая отдельная var/frontend-media-demo-UUID. Переменные относятся только к текущему процессу терминала, файл .env не меняется. Fixture явно authored: artificial silence/solid images, не распознавание живой модели. В ней уже сохранены partial 4/5 и решение needs_research; повреждённый PNG восстановлен, поэтому штатный retry способен дополнить контекст до 5/5 по известным записям. Старые fragments/решение остаются. Закрытие этого терминала не удалит demo; файл demo-settings.json сохраняет несекретные настройки.

Второй PowerShell из корня:

```powershell
Set-Location frontend
npm run dev
```

Если Node не в PATH, но ранее проверенный portable runtime уже подготовлен:

```powershell
$taskNodePath = (Get-Content -Encoding UTF8 ..\var\tooling\node\active-path.txt -Raw).Trim()
$env:PATH = "$taskNodePath;$env:PATH"
& "$taskNodePath\npm.cmd" run dev
```

Открыть http://127.0.0.1:5173. На Linux/macOS — эквивалент ../.venv/bin/python, переменные окружения из demo-settings.json и npm run dev; новых системных настроек/установок скрипт не делает.

Проверить последовательно:

1. В состоянии сервера видны replay/authored и известные ASR/VL записи. Это не live.
2. В форме медиа выбрать manifest.json и **четыре media файла** из выведенного selected-files каталога. Не выбирать manifest как вложение. Preview — unchanged=1; commit подтверждает unchanged=1 без нового source_version. Повтор сохраняет этот счётчик. Смена файла сбрасывает прежнее разрешение.
3. В фильтрах списка выбрать «Все темы»: fixture содержит один источник, поэтому default повторяющиеся темы её скрывает. Открыть карточку, «Показать категории и приоритеты», затем источник.
4. В источнике видны partial 4/5, сохранённая ошибка PNG, unknown, authored. Нажать открытие PNG/аудио. Файл восстановлен, но прежняя ошибка анализа ещё отображается до retry — это разные состояния.
5. Запустить обработку, дождаться completed. Обновить список/карточку/источник: полный контекст 5/5, тот же source/topic, owner/status/история сохранены. Новый обычный repeat использует успешные этапы. Произвольный новый файл может законно дать REPLAY_RECORD_MISSING.
6. Сформировать недельный отчёт и скачать Markdown. Период виден по Москве. Необработанное/unknown/errors не выдаются за normal или отсутствие потребностей.
7. Проверить сохранение решения привычной формой/409 и перезапуск backend с теми же demo переменными. Данные должны остаться.

Остановка — Ctrl+C в обоих терминалах. После закрытия первого терминала его demo переменные исчезают; обычный новый терминал использует прежний .env. Базы не сбрасывать. Настройка боевого live/рассылки и полная корпоративная авторизация не входят в этот показ.

## Фактические проверки

284 обычных frontend tests, TypeScript/build и lint прошли. Четыре React/jsdom действия против настоящего HTTP прошли: var/frontend-media-http-c2e56844c7b1455da12f47822e72fe75/result.json. Подготовка отдельного demo также выполнена; frontend5173/backend8000 ответили200, оба собственных preview-процесса остановлены. Browser Use вернул The browser access host is unavailable, поэтому визуальный прогон не подтверждён. Существующие private файлы не менялись. Отсутствие инструмента не заменяется утверждением «посмотрел в браузере».
