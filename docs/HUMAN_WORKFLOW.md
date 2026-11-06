# Ручное рассмотрение, жизненный цикл, объединение и отчёт

Срез участника 3 от 2026-11-07 для передачи через `feat/server-text-integration` из основной папки. Это backend; UI новых действий пока за участником4. Автоматический поиск/new/промпты U2, медиа U1, live и полный модельный record не реализуются этим срезом. `.env`, рабочие SQLite и чужой код сохраняются. Merge этой feature-ветки выполняет пользователь после PR; он не является частью публикации среза.

## HTTP для участника 4

Все пути ниже относительно `/api/v1`. JSON — UTF-8, ошибки — прежний ErrorResponse(code/message). Нельзя подтверждать сохранение до успешного ответа. Затем читать актуальную карточку/review; при неопределённом результате точный повтор ручной команды безопасен. При 409 не подставлять новую version автоматически: сначала показать актуальные данные человеку.

| Операция | Запрос | Успех |
|---|---|---|
| Чтение review | GET /review-items, GET /review-items/{id} | список/ReviewItem; aliases /reviews сохранены |
| История review | GET /review-items/{id}/history | list[ReviewItem]; alias /reviews/{id}/history |
| Ручное решение review | POST /review-items/{id}/resolve | ReviewItem с сохранённым resolution; alias /reviews/{id}/resolve |
| Бизнес-решение | PATCH /topics/{id}/decision | прежний TopicDetail; теперь все немерженные статусы |
| Просмотр объединения | POST /topics/{id}/merge-preview | MergePreview(source, target, decision_conflict) |
| Подтверждение объединения | POST /topics/{id}/merge | MergeResult с ID/версиями обеих тем |
| Исключение связей | POST /topics/{id}/exclude | ExcludeResult с ID review и новой версией темы |
| История ручных изменений | GET /topics/{id}/changes | list[TopicChangeEvent] |
| Сводка | GET /reports[?date_from=...&date_to=...] (alias /report) | text/markdown; charset=utf-8 |

Список review по умолчанию open; допустим один status=open|resolved|insufficient_data|all. Для POST ручных действий query-параметры запрещены, включая dry_run: ошибочный «preview» не должен молча фиксировать действие. /report принимает только один product_id=webneft/date_from/date_to, обе границы включены по Europe/Moscow. Неизвестные/повторные фильтры — 422. Эти endpoints не меняют форматы импорта/runs.

## Разрешение assignment review

Запрос — ResolveReviewRequest:

```json
{
  "version": 1,
  "action": "attach",
  "author": "Синтетический аналитик",
  "reason": "Проверены источники",
  "topic_id": "ID-выбранной-темы",
  "topic_versions": {"ID-выбранной-темы": 3}
}
```

Это образец, ID/version брать из реальных GET. Выбор темы не ограничен candidate_topic_ids модели. topic_versions содержит **точно все затронутые темы**: выбранную и прежние активные назначения потребностей, если есть. Для обычного unassigned review attach требует только version целевой темы. Лишние/пропущенные ID или stale versions — 409. Тема должна быть немерженной и того же продукта.

`action=create`: вместо topic_id передать proposed_topic (тот же ProposedTopic из schemas.py), topic_versions — для прежних назначений либо `{}`, если их нет. Сервер назначает ID детерминированно из review_id, продукт/связи/начальные бизнес-поля сам; происхождение authored, analysis_mode=null. Manual create не требует полного автоматического поиска. Примеры паспорта должны ссылаться на точные needs/цитаты данного review. Границы паспорта пока scope_confirmed=false; не выдавать их за подтверждённые моделью. Версию созданной темы читать GET: запись ручных overrides может увеличить её, не предполагать version=1 в ответе конечного действия.

`action=insufficient_data`: topic_id/proposed_topic не передаются. Запись получает status=insufficient_data и явную причину, ссылки не добавляются. Если исправляются уже существующие назначения, их версии тоже нужны: связи снимаются атомарно. Потребности не исчезают из источников.

Автор/причина непустые; version — строго целое >=1. resolved_at/request_hash/новые ID серверные, клиент их не посылает. Разрешение сохраняет связи/overrides/историю/закрытие review в одной транзакции. Изменённый источник или requires_recheck — 409; нужен новый review для новой версии. Точный повтор уже подтверждённой команды возвращает сохранённое решение без новых overrides/истории. После последующего обновления источника такой ответ может иметь requires_recheck=true: это не повторное применение старого решения к новому тексту.

Разрешение duplicate review (merge/keep_separate) остаётся необязательным дополнением и сейчас возвращает 501. Обычное объединение тем доступно отдельной операцией ниже. Автоматические helpers U2 в `review.py` не переписаны и не являются HTTP-сервисом.

## Остальные бизнес-статусы

PATCH использует прежний DecisionUpdate: status, owner, reason, version; дополнительные поля по статусу. Теперь доступны new, needs_research, linked, deferred, declined, implemented. merged через PATCH запрещён, его выставляет только merge.

Для linked обязателен existing_issue_ref. Простой ID вроде WEB-123 допустим; URL — только HTTPS, хост из APP_ISSUE_HOSTS (через запятую), без credentials/нестандартного порта. Никакого перехода по ссылке сервер не выполняет. Для deferred обязателен review_after (date); для implemented — implemented_at с timezone и implementation_comment. Примеры — data/contracts/workflow-examples.json. История/owner/reason/version сохраняются прежним Repository.record_decision.

review_due вычисляется по московской дате. Новые обращения после решения/внедрения считаются по created_at, а не времени импорта; строгое равенство implemented_at не считается «после». Поздно загруженное старое обращение не создаёт ложного сигнала. Статус не меняется автоматически. Declined — бизнес-решение; оно не создаёт ошибку анализа или review.

## Объединение тем

Сначала POST merge-preview с target_topic_id. Ответ содержит обе реальные карточки/истории и decision_conflict. Показать человеку **сохраняемое решение целевой темы**, последствия для исходной и обе версии. Наличие решения у исходной темы при отсутствии его у целевой тоже требует подтверждения: новое пустое решение не должно молча скрыть прежнее.

```json
{
  "version": 3,
  "target_topic_id": "ID-целевой-темы",
  "target_version": 2,
  "author": "Синтетический аналитик",
  "reason": "Потребности проверены и признаны одной темой",
  "confirm_decision_conflict": true
}
```

confirm_decision_conflict — строгий boolean, по умолчанию false. Если preview показал конфликт и человек не подтвердил — 409 MERGE_DECISION_CONFLICT. Изменение любой карточки после просмотра — 409, нужен новый preview. Self-merge/разные продукты — 422; merged source/target — 409. Цепочки не замыкаются в цикл.

Commit переносит активные связи и сохраняет исторические ссылки/pending/provenance, дописывает ручные attach-overrides и историю обеих тем, исходная получает merged/canonical ID. Target passport, решение и DecisionEvent-история не переписываются. При смеси authored/model целевое происхождение authored не скрывается. Счётчики — distinct source_id, две потребности одного обращения не превращаются в два обращения. Каталог меняет revision.

Точный повтор команды возвращает тот же небольшой MergeResult без повторного переноса. Result — квитанция уже выполненного действия; перед следующим действием читать свежие GET. Старый ID остаётся доступен как merged карточка с canonical_topic_id конечной основной темы, даже после цепочки объединений. Редактировать решение следует в основной карточке. Полный source history не уничтожается.

## Исключение неверной связи

POST exclude принимает version темы, непустой список уникальных need_ids (до100), author/reason. Только актуальные активные потребности этой темы. Сервер снимает выбранные связи, сохраняет ручной запрет прежнего назначения и открывает assignment review **вместе**. При сбое не остаётся частичного исключения; точный повтор не создаёт новую очередь. После изменения source_version прежний запрет/review помечается recheck; его нельзя молча перенести на другой текст. Повторный pipeline не возвращает старую связь. Явное новое решение человека может изменить прежний ручной выбор с новой историей.

Комментарий или declined не выполняют exclude. Отдельный taxonomy/API AnalysisFeedback требует сверки с U2 и этим срезом не подменяется.

## Отчёт

GET /report и CLI `app.cli report` используют одну функцию и единый SQLite snapshot, без моделей/init/migration. Подсчитываются уникальные источники и заявители (при неполных requester_id — «нет данных»), активные needs, данные по темам за период и за всё время, pending/review, ошибки материалов и последние десять запусков. Последние запуски помечены отдельно: они не фильтруются периодом обращений. Coverage run относится к подготовке; errors/state показываются даже при coverage=1.

Markdown экранирует недоверенные поля и не включает поля исходного текста/цитат/настроек подключения. Синтетика/authored/replay видны, качество/экономия не объявляются измеренными. Бизнес-числа берутся из SQLite, не сгенерированных строк. Фильтры применяются по московской дате created_at.

```powershell
# Из backend, APP_DB_PATH — уже подготовленная отдельная база.
..\.venv\Scripts\python.exe -X utf8 -m app.cli report --date-from 2026-10-01 --date-to 2026-10-31 --output ../var/report-check.md
```

--output создаёт **новый** UTF-8 файл; существующий файл не перезаписывается, чтобы ошибочный путь не уничтожил SQLite/.env. Для следующего отчёта выбрать новое имя. Без --output текст выводится в терминал. Отсутствующая БД явно ошибочна и не создаётся отчётом.

## Безопасная терминальная проверка

В PowerShell, из основного проекта:

```powershell
Set-Location C:\Users\user\projects\GPN_Insight\backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -X utf8 -m app.cli export-schemas
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_human_workflow
```

Последняя процедура сама создаёт synthetic/authored SQLite, запускает свой ordinary app.main:app на свободном localhost-порту, выполняет реальные HTTP и CLI, перезапускает сервер и останавливает его. Рабочая база/.env не используются. Она проверяет deferred/409 → exclude count3/repeat → manual create/repeat → merge preview/409/confirm/count4/target history → report → restart. Ожидаемые строки: `synthetic=true; ... model_calls=none`, `decision: deferred=200`, `merge: ... target decision/history/count=4 preserved`, `PASS: actual manual workflow HTTP/CLI/restart...`. Новый report/result.json появляется в уникальной var/human-workflow-check-*.

Этот сценарий **не проверяет live или auto-new U2**: темы создаёт явно человек, POST runs в этом сервере остаётся честным503 без настроек. Отдельные прежние check_runtime_binding/check_pipeline продолжают проверять authored replay и interrupted/reuse.

## Передача и оставшаяся интеграция

U2: серверный ProposedTopic/propose_topic и правила manual overrides готовы. Нужны его полный поиск, фактический размер пакетов и proposal-промпт/new. Пользователь подтвердил 2026-11-07: продолжение ещё не опубликовано, подготовить передачу. Контракт — TEAM_INTEGRATION.md; full model record/quality нельзя завершить без этого и проверенного gateway/набора.

U4: подключить новые endpoints после просмотра этого контракта, версий и примеров; показать merge-preview/подтверждение, 409 без слепого retry, review recheck, отдельную историю ручных действий и сводку. Backend не объявляет готовым UI этих действий. Старые /reviews/new/needs_research совместимы.

U1: формат импорта/хеши/файловый протокол не менялись. Модели/медиа и восстановление файлов остаются его интеграцией с сервером. Секреты не нужны в JSON-примерах или Git.

SQLite остаётся v3: расширены только optional JSON-поля ReviewResolution.request_hash и TopicChangeEvent.request_hash/result/kind. Старые записи читаются; новые ручные записи следует читать актуальным backend. Нет сброса/миграции рабочих данных.
