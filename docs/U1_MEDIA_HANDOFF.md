# Передача U3/U4: реализованный медиаимпорт U1

Обновление 09.11: standalone media Normalizer, строгий ASR/VL replay, штатный partial Pipeline и UI уже подключены; старые формулировки ниже фиксируют границу первоначального импорта. Итоговая приёмка и расширенные проверки У1 — [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md). Обезличенность входа принимается по условию кейса; полное собственное обезличивание не является требованием прототипа. Synthetic-only/access ограничения текущей реализации сохраняются.

Приёмка U3 08.11.2026 поверх main `6f3b8df`: media import уже объединён PR #12, frontend-фильтры/паспорт — PR #13. Реальные Windows HTTP/CLI/restart пройдены на отдельной synthetic БД с сохранением решений и зарегистрированных файлов. Точные pins/байты/однопроцессное ограничение приняты; исправлены content-check staged upload и переносимость проверок. Постоянный resolver и актуальная граница — [MEDIA_RUNTIME.md](MEDIA_RUNTIME.md). Следующие датированные статусы относятся к этапу до merge; публикация текущих серверных правок пока не выполнялась.

2026-11-07, ветка `feat/u1-media-import` от `60eedf3`. HTTP/CLI импорт WAV/MP3/PNG/JPEG реализован по прямому поручению пользователя. Новые версии парсеров выбраны и проверены по его отдельному разрешению. Это не сообщение, отправленное команде, и не утверждение о независимой приёмке U3/U4.

Основной контракт и точные byte-лимиты — [IMPORT_HTTP.md](IMPORT_HTTP.md); реализация/решения/приёмка — [.agent/U1_MEDIA_IMPORT.md](../.agent/U1_MEDIA_IMPORT.md). Исторический план передачи сохранён в локальном снимке предыдущих правок; ниже текущее поведение.

## Ответ на запросы U2 и U4 — проверено 07.11.2026

| Запрос | Фактический результат | Что остаётся |
|---|---|---|
| U2: контракт media-источников, metadata и provenance | Подготовлен [MEDIA_ANALYSIS_HANDOFF.md](MEDIA_ANALYSIS_HANDOFF.md) и [проверяемые примеры четырёх форматов](../data/contracts/media-analysis-handoff.json) по существующим shared DTO | Подтверждение U2/U3 будущего media-профиля, partial-error policy и реального протокола моделей; эти решения не подменяются документом |
| U4: API предварительной проверки и импорта текста в main | Уже в GitHub main `60eedf348f8f73664186308e8f99c01c6b4420e3`: POST `/api/v1/imports?dry_run=true`, POST `/api/v1/imports`, GET `/api/v1/imports` | Приёмка и подключение формы U4 в браузере |
| U4: те же API с WAV/MP3/PNG/JPEG в main | Расширение реализовано и проверено локально в `feat/u1-media-import`; HTTP-контракт/примеры готовы | Проверка командой и разрешённый PR/merge; в main media-расширения пока нет |

GitHub compare HEAD→main дал identical. Наличие router проверено в [main.py на проверенном коммите](https://github.com/anvs11/GPN_Insight/blob/60eedf348f8f73664186308e8f99c01c6b4420e3/backend/app/main.py), сами endpoints и отказ файлов в main — в [imports_api.py](https://github.com/anvs11/GPN_Insight/blob/60eedf348f8f73664186308e8f99c01c6b4420e3/backend/app/imports_api.py). Проверка исходников не является проверкой развёрнутого сервиса. Локальные команды ручной приёмки и ожидаемые ответы — [MEDIA_IMPORT_CHECK.md](MEDIA_IMPORT_CHECK.md).

Готовый текст для передачи U2: «Контракт источников/RegisteredMaterial/ApprovedAsset/MediaText/NormalizedDocument и provenance описан в MEDIA_ANALYSIS_HANDOFF.md; четыре authored примера проверяются scripts.check_media_handoff. Реального media-профиля/recordings пока нет. Для подключения согласуем partial errors, идентичность/версию media recipe и фактические ASR/VL протоколы с U3».

Готовый текст для передачи U4: «Текстовые preview/commit/history уже в main 60eedf3; media-расширение тех же API пока локальное. IMPORT_HTTP.md содержит формы, byte-лимиты и ошибки; U1_MEDIA_HANDOFF.md — поведение UI; MEDIA_IMPORT_CHECK.md — воспроизводимая серверная проверка. Binary-source API/распознавание этим импортом не предоставляются». Эти тексты не отправлены; commit/push/merge не выполнялись.

## Что передать U3

Переиспользованы MaterialInput/TicketInput/RegisteredMaterial/DatasetRegistration, ImportPreview/ImportResult/ErrorResponse, SQLite v3, source_content_hash, import_commit/register_dataset/registered_materials. Эти shared DTO/хранение/guard не менялись. Новые внутренние `ImportWorkspace`, `StagedUpload`, `RecoveryReport` находятся в `ingestion/media_files.py`, формат внешнего ответа не расширялся. `security.py` содержит общие правила имён, no-follow/identity-проверки файлов, лимиты и media parsing. ApprovedAsset создаётся только после проверки bytes/метаданных/hash; к моделям он не передаётся.

HTTP сначала считает фактические байты и сохраняет сырой конверт в собственный временный файл, до parser. Затем callbacks порциями пишут files в staged-файлы с лимитами; manifest ≤1 МиБ держится в памяти. При закрытии workspace handles и временные файлы убираются, при неопределённости final/журнал сохраняются. `import_tickets(..., workspace=...)` проверяет точное соответствие assets/files и создаёт один готовый DatasetRegistration. CLI `import_from_directory` читает только перечисленные простые имена внутри каталога manifest и вызывает тот же importer.

Новые server UUID каталоги в uploads создаются эксклюзивно. Tmp/uploads раздельны и находятся на одном диске. Перед SQL материал переносится через link(EXCL)+unlink, проверяются стабильность staged-файла и SHA-256 final-файла. Journal до файлов фиксирует planned-пути и `(st_dev, st_ino)` эксклюзивно созданного final-каталога. Это не даёт принять существующую коллизию за свои файлы. Файловая система должна поддерживать hard links; проверено на macOS/APFS, Windows runtime ещё не проверен.

Весь finalize выполняется одним синхронным вызовом под прежним реентерабельным import_commit; чтение/перемещение файлов не входят в короткую SQL-транзакцию. После любого исхода cleanup перечитывает полный registered_materials без фильтра по продукту/текущей версии. Старые канонические пути и исторические материалы сохраняются, новая лишняя копия убирается только при доказанном владении. При потере ответа новый пользовательский UUID даёт безопасный repeat; exact registration_id требует того же batch со storage_path, не новых путей.

Изменения в зоне U3 ограничены подключениями: `cli.py` вызывает общий directory adapter и выводит безопасные RepositoryError.message; `main.py` после initialize/interrupt_active_runs делает reconcile до yield; `api.py` добавляет storage issues в существующий health.integration_errors. Pipeline/Normalizer/analysis/prompts/SQL/recording policy не изменялись. Не сбрасывается готовность текстового pipeline из-за media-health; импортные 503 дают код/действие.

Uploads имеют `.import-owner.json`, связанный с каноническим DB-path hash; временные запросы несут тот же owner в собственном журнале, отдельный постоянный marker в preview не создаётся. Каждая БД использует собственные roots. Неизвестные/legacy непустые roots, чужая БД, неоднозначные/битые журналы, symlinks и недоступный реестр не разрешают автоматическое удаление. Проблемы сохраняются в `var/reports/import-recovery-*.json`, включая asset ID для зарегистрированных повреждений. Подмена/перенос SQLite требует отдельной проверки принадлежности: это не автоматический migration/repair.

Media CLI над той же БД выполняется при остановленных сервере/других CLI. Guard остаётся process-local; межпроцессный lock не добавлялся. CLI dry-run сверяется read-only. После убийства внутри SQLite штатный startup/init-db существующей БД должен сначала восстановить hot journal; импорт сам не создаёт/мигрирует отсутствующую БД. Зарегистрированные повреждённые файлы не восстанавливаются молча свежей загрузкой.

## Зафиксированные зависимости и проверки

Новые pins: **Pillow==13.0.0**, **mutagen==1.48.1** в requirements.in и requirements.txt. Выбор основан на [официальном PyPI Pillow](https://pypi.org/project/pillow/) и [PyPI mutagen](https://pypi.org/project/mutagen/), а совместимость проверена установкой и тестами на CPython 3.13.0/macOS ARM64. Существующие 25 версий не обновлялись, установка выполнялась с --no-deps. WAV разбирается стандартным wave, images — Pillow verify+load, MP3 — mutagen metadata. Полного MP3-декодера/ASR здесь нет; успешный импорт не обещает распознавание.

Получены **652 passed, 1 прежний Starlette/AnyIO warning**, pip check без конфликтов, 16 общих/7 HTTP/ProposedTopic/14 workflow-примеров валидны. В новых тестах проверены размеры/количество, путь/имена/ссылки, изменение staged bytes, preview, порядок частей, MIME клиента, repeated/delta, сохранность решения/ручной истории, реальный process-stop до/после commit, link-window, неизвестный registry/journal/owner и collision существующего final-каталога.

Последнее ревью и ручной curl/CLI/restart: `var/reviews/u1-media-prehandoff-20261107/REVIEW.md`, `manual/result.json`; 652 passed и сохранность решения/истории/5 hashes. Команды воспроизведения — [MEDIA_IMPORT_CHECK.md](MEDIA_IMPORT_CHECK.md). Следующие отчёты относятся к первоначальной реализации до ревью.

Обычный Uvicorn/HTTP/CLI/restart на synthetic SQLite: `var/media-import-check-e6b04bc08db0402295051367f222a22f/result.json`. Прежний текстовый HTTP-сценарий также PASS: `var/import-http-check-77ddb8de531b438ba399ae1c3dd08dac/result.json`. Все команды/логи — `var/reviews/u1-media-implementation-20261107/`; эти локальные отчёты не коммитятся. Windows, Node/npm, frontend и браузерный проход не проверялись; старый отдельный check_text_normalizer по-прежнему имеет известный v1/v3 handoff-конфликт и в этой задаче не исправлялся.

Из backend воспроизвести:

```sh
../.venv/bin/python -m pip check
../.venv/bin/python -X utf8 -m pytest -q
../.venv/bin/python -X utf8 -m app.cli validate-examples
../.venv/bin/python -X utf8 -m scripts.check_media_import
```

Последний скрипт сам изолирует SQLite/uploads/tmp и процессы localhost. Синтетический набор — [data/media-import](../data/media-import/README.md), прежний data/demo не заменён. Независимые данные/качество/польза не измерялись.

Результат повторного ревью текущего diff в этой сессии: 652 tests, ручной curl/CLI/restart, 422/503 без изменения SQLite, сохранность owner/decision/history и пяти registered hash после delta. Проблемы исправлены внутри U1: owner/registry JSON → 503, глубокий journal → безопасная ошибка, проверка reuse по текущему продукту при полном реестре cleanup, reserved name с пробелом. Команды — [MEDIA_IMPORT_CHECK.md](MEDIA_IMPORT_CHECK.md), новые отчёты — var/reviews/u1-media-prehandoff-20261107/. Это наша проверка, не приписанная U3/U4 приёмка.

## Конкретное подключение U4

Пути прежние: один manifest и до 30 повторяющихся files. Имена точно соответствуют asset.path; порядок частей свободный. Manifest не содержит server storage_path, registration_id, origin или выбора модели. source_url не скачивается. Пример FormData:

```ts
const form = new FormData()
form.append('manifest', manifestFile)
for (const file of selectedFiles) form.append('files', file)
const response = await fetch('/api/v1/imports?dry_run=true', {
  method: 'POST', body: form,
})
```

Не выставлять Content-Type вручную, boundary добавляет браузер. Использовать единый frontend API layer. Для commit — новый FormData с текущим выбором на POST `/api/v1/imports`, без dry_run; preview-token/сессии нет. Смена JSON или любого media сбрасывает preview. До ответа commit не показывать «сохранено»; после него показывать фактические counts, которые могут отличаться от предварительных.

| Ответ | Действие UI |
|---|---|
| 200 valid preview | Показать предварительные обращения/предупреждения, предложить отдельное подтверждение |
| 200 invalid preview | Показать code/message, can_commit=false; partial import отсутствует |
| 200 ImportResult | Показать фактические counts и перечитать нужное состояние сервера |
| 413 | Уменьшить размер/количество; успех не подтверждать |
| 422 | Исправить manifest/файл/состав/query по безопасному русскому сообщению |
| 409 | Дождаться обработки/фиксации либо перечитать конфликтующую версию, сохранить выбор |
| 503 storage/integrity | Предложить проверку хранения, не трактовать как отсутствие потребностей |
| Обрыв или IMPORT_CLEANUP_PENDING | Результат может быть сохранён; показать неопределённость/действие и явный безопасный repeat |

Первая ошибка даёт invalid=1 и остальные counts=0; полного списка всех ошибок пока нет. Counts относятся к обращениям, не файлам. Клиентская предварительная проверка не заменяет серверную; hashing, grouping и модельные вызовы в браузер не переносятся. Не выводить недоверенный текст/имена как HTML.

Binary GET assets и просмотр media не добавлены. Не строить URL из storage_path или имени клиента; SourceViewer пока показывает метаданные и существующее ограничение просмотра. MEDIA_NORMALIZATION_NOT_SUPPORTED/MEDIA_PIPELINE_NOT_IMPLEMENTED остаются явными ошибками следующего этапа. Preview не включает анализ; успешный commit не является распознаванием.

Для приёмки U4 открыть UI: JSON+WAV+MP3+PNG+JPEG → preview → commit → фактические counts → repeat/unchanged → delta → проверка решения/restart. Проверить смену выбора, обрыв, 409/413/422/503 и отсутствие ложного успеха. Браузерный прогон U4, Windows и интеграционный review U3 остаются невыполненными. Сообщения коллегам, commit/push/merge не выполнялись.
