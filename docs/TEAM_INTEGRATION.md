# Сверка команды и серверный стык, обновлено 2026-11-07

Текущий U3-срез дополнительно завершил независимые от UI/медиа функции: фильтры GET /topics и периода карточки, PATCH паспорта и сценарный record/verify (9 шагов/11 authored записей). 587 tests, 38 schemas/14 workflow-примеров и настоящий ordinary HTTP/CLI/restart повторно прошли перед публикацией. Передача U4/U1/U2 и точные команды — [SERVER_COMPLETION.md](SERVER_COMPLETION.md); план — [.agent/SERVER_COMPLETION.md](../.agent/SERVER_COMPLETION.md). Прежние локальные исправления и рабочие .env/SQLite сохранены. По поручению пользователя проверенный срез передаётся через feat/server-text-integration; merge он выполняет самостоятельно. Следующая интеграция требует реального gateway/данных качества, media U1 или UI U4.

Холодный старт завершён по дополнительному поручению пользователя, согласованному с U2. На пустом каталоге AnalysisService отправляет assign_topic с topics=[], затем только после явного provisional_no_match вызывает propose_topic. Review остаётся review. Доказательства/provenance/атомарное сохранение не обходятся; ID, new/v1/owner=null задаёт Pipeline. 541 backend tests passed, один прежний warning; настоящий ordinary Uvicorn/strict authored replay на пустой базе подтвердил первую тему, match второго обращения, count2, решение/409, повтор/restart/6 reused и явную ошибку неизвестного запроса. Отчёт var/cold-start-check-599ed3c6fbf84876b6f3814d7ba8c1d7/result.json; план [.agent/COLD_START.md](../.agent/COLD_START.md). Live и независимое качество не измерялись. Дальнейшие ссылки на прежнее ожидание U2 ниже считаются историческими.

Актуальный срез 2026-11-07: PR #9 U3 и PR #10 U2 входят в загруженный main 8f69c7d. Существующая feat/server-text-integration обновлена до него fast-forward. U3 исправил подключение реального propose.md и совместимость старых new_topic checkpoints. Реальный ordinary Uvicorn/HTTP с отдельными synthetic/authored replay и SQLite прошёл new → решение → match нового обращения → повтор → рестарт/6 reused → неизвестный запрос с REPLAY_RECORD_MISSING. .env и рабочие SQLite не менялись. Новые исправления пока локальные; план — [.agent/PROPOSAL_INTEGRATION_FIX.md](../.agent/PROPOSAL_INTEGRATION_FIX.md). Ручной workflow — [HUMAN_WORKFLOW.md](HUMAN_WORKFLOW.md).

Первичная сверка выполнена в отдельном клоне на основе main e4e67e6 и U1-коммитов 72606af/4326de5. Затем U1 объединён/отправлен в main (e0d75e9/cad1574), а новые серверные контракты U3 перенесены в единственную основную папку C:/Users/user/projects/GPN_Insight на feat/server-text-integration. Прежний клон — архив проверки без собственного .git, не место разработки. Передача нового среза выполняется из основной папки; .env/базы/окружение в Git не входят.

## Что слито и что ещё в ветках

PR [#7](https://github.com/anvs11/GPN_Insight/pull/7), [#8](https://github.com/anvs11/GPN_Insight/pull/8), [#9](https://github.com/anvs11/GPN_Insight/pull/9) и [#10](https://github.com/anvs11/GPN_Insight/pull/10), включая U2 6ad2358, уже присутствуют в локальном main. U1-ветки также входят в main. Оставшиеся имена веток не означают вторые версии проекта и не требуют повторного merge. Новые исправления U3 выполняются в существующей feat/server-text-integration от 8f69c7d. Перед следующей публикацией сверять remote заново: коллеги могут продолжить работу.

| Участник | Фактически существует | Что ещё нужно |
|---|---|---|
| 1 | В main: строгий preview, текстовый commit, masking/TextNormalizer, проверки границ Analysis, HTTP/CLI импорта, bootstrap утверждённого профиля | Медиа/ASR/VL и файловое восстановление остаются отдельными этапами |
| 2 | NeedExtractor, ранжирование всего каталога, batching полного JSON с оценкой запроса, match/review/new, propose.md, evidence validation и benchmark-инструменты; cold-start добавлен U3 по согласованию | Размеченный development/holdout и измерение качества; проверка бюджетов на реальном протоколе/токенизаторе |
| 3 | SQLite v3, API/runs/replay, ProposedTopic→Topic, ручной workflow, исправленное подключение промпта/legacy checkpoint и проверенный текстовый new→match | Передача локальных исправлений; внутренний live gateway после проверки протокола, полный model record и общая сборка |
| 4 | Реальные API тем/источников/решений, version/409 и проверка сохранения, панель runs | Форма импорта по IMPORT_HTTP, очередь review и согласованное действие её разрешения; frontend в этой задаче не реализуется заново |

Сообщение о том, что у U2 есть только extractor, уже устарело. При этом `build_analysis` в `adapters/text.py` — фабрика AnalysisService, **не сетевой Qwen TextGateway**. Его наличие не подтверждает доступность модели.

## Что доработал участник 3

`schemas.ProposedTopic` — новый строгий модельный ответ. `AssignmentResult` принимает `proposed_topic` при decision=new; старый `new_topic` оставлен для старых адаптеров и сохранённых checkpoints, оба одновременно запрещены. Pipeline назначает постоянный ID, продукт, need_ids/evidence и new/owner=null/version=1. Паспорт получает version=1 и scope_confirmed=false; модель не подтверждает границы от имени человека. Доказательства примеров проверяются по конкретной назначаемой потребности. Создание и assignment checkpoint фиксируются одной SQLite-транзакцией, при ошибке откатываются вместе. Миграция схемы БД не требуется.

Операция TextRequest `propose_topic` принимает только ответ ProposedTopic в replay registry. Прежние ExtractedNeeds/BatchAssignment и алгоритм request hash сохранены. Реальный `backend/prompts/propose.md` U2 теперь включается в профиль по умолчанию с версией `propose-topic-v1`; тот же путь передаётся AnalysisService. Если при запуске процесса задан APP_PROPOSE_PROMPT_PATH, профиль и сервис используют этот override. Для запуска через .env эта переменная не поддерживается Settings: не добавлять её туда как работающую настройку. При смене промптов готовятся новый профиль и точные записи с новой recording_set_version в отдельном проверочном каталоге. Старые профили/записи не изменяются, конфликт явно отклоняется.

ProposedTopic требует проверенных metadata assign_topic и propose_topic. Старый new_topic допускает прежний единственный assign_topic и не получает выдуманного происхождения; присутствующие дополнительные proposal metadata тоже проверяются. В live origin=authored остаётся недопустимым. Ни SQL-миграции, ни переписи checkpoint нет.

Сохранены GET /reviews и /reviews/{review_id}; теперь есть aliases /review-items и POST resolve с атомарными действиями/историей. По умолчанию status=open; допустим один status=open|resolved|insufficient_data|all. Неизвестный ID — 404, неверные фильтры — 422. UI может подключать настоящее разрешение по HUMAN_WORKFLOW, но backend не объявляет его уже подключённым в браузере.

Pipeline теперь пропускает extraction для документа с MaterialError независимо от наличия защитной обёртки U1: ошибки остаются видимыми, needs=[] не выдаётся за успех. Проверяются searched_topic_count относительно реально перечисленных passport_versions и полный набор паспортов при заявленной полноте; при new кандидатов быть не должно. Чужие алгоритмы и промпты U2 не переписывались.

## Ответ участнику 2: интерфейс и ProposedTopic

Серверный интерфейс остаётся прежним:

```python
Analysis.extract_needs(document: NormalizedDocument) -> ExtractionResult
Analysis.assign_topic(need: ExtractedNeed, topic_passports: CatalogSnapshot) -> AssignmentResult
```

Analysis не пишет в SQLite и не назначает постоянные ID/бизнес-статусы. Один worker сначала извлекает все потребности, затем вызывает assign_topic в порядке created_at/source_id/индекс потребности. Snapshot содержит немерженные темы и catalog_revision; после модельного вызова Pipeline повторно проверяет ревизию в транзакции. Изменение каталога даёт CATALOG_CHANGED; решения человека не перезаписываются.

ProposedTopic содержит `title`, `summary`, `scenario`, `need_statement`, `scope`, непустой `inclusion_examples`, необязательный `exclusion_examples=[]`. Пример — существующий PassportExample(text, evidence_refs). На первом создании ссылки каждого примера должны точно совпадать с `(need.source_id, need.fragment_id, need.evidence_quote)` назначаемой потребности. Не выдумывать отрицательные примеры: если их нет в источнике, оставить пустой массив. Полей topic_id/product_id/version/status/owner/scope_confirmed/need_ids в ответе нет; extra запрещены. Синтетический JSON — `data/contracts/topic-proposal.json`.

После **полного** поиска без match/review-кандидатов U2 может отдельно вызвать:

```python
result = gateway.generate(TextRequest(
    operation="propose_topic", prompt=proposal_prompt,
    prompt_version="propose-topic-v1",
    data={"need": need.model_dump(mode="json"),
          "catalog_revision": snapshot.catalog_revision,
          "passport_versions": searched_versions},
    parameters=profile.parameters,
), ProposedTopic)
# Итог AnalysisService, не сырой ответ модели:
return AssignmentResult(
    assignment=server_checked_new_assignment,
    proposed_topic=result.value,
    metadata=[*batch_metadata, result.metadata],
)
```

`server_checked_new_assignment` здесь обозначает TopicAssignment, который должен построить AnalysisService U2: need_id, mode, catalog_revision и версии из переданного snapshot; searched_topic_count=len(snapshot.topics), candidate_set_complete=true, search_truncated=false, candidate_topic_ids=[], matched_topic_id=null, точный evidence_ref и decision=new. Это иллюстрация интерфейса, не уже реализованный метод U2. Модель не получает право выставлять признаки полноты: BatchAssignment по-прежнему не содержит new. Для неполного поиска, разногласий пакетов, неопределённости — review без proposal.

В profile.prompt_hashes нужен SHA-256 **реального UTF-8 содержимого** propose.md под ключом propose_topic. Metadata результата должны включать проверенные ответы поиска и предложения, модель/параметры/нормализацию/версию набора. Все ответы live имеют origin=model; authored в replay виден явно. При изменении набора/промптов используется новая recording_set_version и отдельная чистая проверочная БД; старые опубликованные результаты не переанализируются молча с другими паспортами.

Контракт U2 подтверждён: AnalysisService возвращает ProposedTopic с проверяемыми evidence, сервер назначает постоянство/бизнес-поля и фиксирует тему с checkpoint атомарно. По дополнительному согласованию cold-start реализован тем же путём, без изменения prompts/DTO/HTTP/SQL. INTEGRATION_VERSION теперь u2-text-integration-v3: изменение политики отражено в точном профиле и ключах. Перед запуском требуется новый проверенный профиль и recording_set_version; старые опубликованные записи/БД не переписывались, автоматического fallback нет. Проверка missing proposal record подтверждает отсутствие темы/assignment checkpoint при ошибке, а явный повтор с полным тестовым набором переиспользует два успешных этапа. UI новых действий принадлежит U4, live не объявляется рабочим без gateway/настроек.

Текст для U2: «По согласованию добавили cold-start в твоём AnalysisService: assign_topic на пустом каталоге → явный provisional_no_match → отдельный propose_topic → серверное сохранение. Review/ошибки не превращаются в new. Проверили evidence, отсутствие записи, retry без повторного extraction, new→match, решения и restart на строгом authored replay. Схемы, промпты и HTTP не меняли; политика теперь v3, для неё нужен новый профиль/набор записей. Полный backend: 541 passed. Качество live-модели этим не измеряли. Изменения пока локальные в feat/server-text-integration».

## Замечания участникам 1 и 2

У U2 появились собственные проверки принадлежности цитат и ошибочного документа; `_CheckedAnalysis`, Pipeline и Repository остаются дополнительными серверными барьерами. Изменения алгоритмов U2 в этом срезе не выполнялись.

Retrieval ранжирует весь каталог; ограничения пакетов применяются после ранжирования. Batching учитывает полный Topic JSON и оценивает prompt/need/parameters по UTF-8, но это ещё не проверенный лимит реальной модели с её токенизатором/response schema. При неполном поиске автоматический new запрещён. `review.py` остаётся helper-политикой U2, а HTTP/транзакционные ручные операции реализует HumanWorkflow U3.

Для вопроса об эталоне: data/demo/manifest.json, analysis-handoff и authored replay — синтетические примеры, не размеченный независимый benchmark. Участники 1/2 должны заранее разделить development/holdout, разметить потребности, точные masked-фрагменты/цитаты, тип/признаки, эталонные темы/исходы, включая несколько потребностей, пустой результат, близкие разные темы и неоднозначности. Текущая extraction_metrics сравнивает точные нормализованные формулировки; она не измеряет семантическую полноту сама по себе. Значения качества/обобщения без набора и прогона не объявляются полученными. Разметка и алгоритмы оценки не делались за U1/U2.

## Выполненная проверка и воспроизведение

После консолидации всё повторено **в основной папке**, без окружения/БД клона: 478 passed, 16/7 + ProposedTopic, 28 схем, pip check, ordinary HTTP/runtime/restart и tests-only interrupted/reuse. Новые отчёты: var/runtime-binding-check-d7286a5ce2cd4881bdbc3185575eae48/result.json и var/pipeline-check-0870db776b114ead8265317304ed008f/result.json. Проверка U1 перед merge: var/import-http-check-0eed9f63a3364e74840b5f2e5e50c262/result.json. .env/четыре рабочие SQLite сохранились по SHA-256, исходные снимки и Git metadata старого клона — var/backups/unify-20261106. Вложенный клон теперь архив без собственного .git; разрабатывать и запускать команды следует только из основного корня.

Windows, Python 3.13.0. Исходная объединённая сборка: 459 passed. После серверных дополнений: **478 passed**, один прежний DeprecationWarning Starlette/AnyIO; `pip check` без конфликтов, 16 общих/7 HTTP-примеров и ProposedTopic валидны, экспорт 28 схем. 19 новых проверок покрывают server-only поля, legacy/checkpoints, new/решение/409/reopen, чужие примеры, provenance, rollback, ошибочную нормализацию, точный proposal replay и GET review. Алгоритмы/промпты U2 и frontend/src сохранены.

Из корня **основной рабочей папки проекта**, в PowerShell:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
..\.venv\Scripts\python.exe -X utf8 -m app.cli export-schemas
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_import_http
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_runtime_binding
..\.venv\Scripts\python.exe -X utf8 -m scripts.check_pipeline
```

Ожидание: 478 passed (время может отличаться), примеры valid, No broken requirements found; затем PASS. Процедуры сами создают отдельные SQLite/recordings и запускают/останавливают собственные localhost-процессы; рабочая база не используется. На другой ОС Python — `../.venv/bin/python`; этот новый общий срез на macOS отдельно не запускался.

`check_runtime_binding` проверяет обычный app.main:app без подмены PipelineAdapters: импорт4 → completed на authored replay → решение → unchanged4/12 reused → рестарт/12 reused → неизвестная delta с REPLAY_RECORD_MISSING → live503 без fallback. Warm-start содержит отдельно подготовленную authored-тему, поэтому count=5, а не результат создания темы живой моделью. `check_pipeline` использует tests-only адаптеры и настоящее прерывание процесса, проверяет interrupted и повторное использование успешных шагов. Проверка ProposedTopic в тестах использует отдельно обозначенный синтетический Analysis: production-алгоритм new ещё за U2.

Отчёты этой сессии в var клона: `runtime-binding-check-28653a0c65a14fc2b15a6dc36550a5f7/result.json`, `import-http-check-224cd4e54e8e4d40978463dddc725277/result.json`, `pipeline-check-bf044f14cb84404da0b47272b0f04eb4/result.json`, `text-pipeline-check-efc6cb415ad14beebdc39fa2ed36f9e2/result.json`. Также прошли check_text_import/check_text_normalizer/check_import_preview. Для контракта proposal реально выполнены:

```powershell
$recordings = '../var/proposal-contract-' + [guid]::NewGuid().ToString('N')
..\.venv\Scripts\python.exe -X utf8 -m app.cli record --authored-input ../data/recordings/examples/topic-proposal-authored.json --recordings $recordings
..\.venv\Scripts\python.exe -X utf8 -m app.cli verify-recordings --recordings $recordings --requests ../data/recordings/examples/topic-proposal-authored.json
```

Ожидание: record_count=1, origins=[authored], network_used=false, verified_requests=1. Пример contract-only не подходит как рабочий профиль анализа.

Frontend/src просмотрен на соответствие текущему HTTP, но Node.js/npm в этой среде не найдены: frontend tests/build и визуальный браузерный прогон здесь **не выполнялись**. Сохранённые результаты U4 не подменяются собственной проверкой. Участнику 4 переданы API импорта и чтения review; для визуального прогона нужны его npm ci/test/build и запущенный UI. Python backend от Node не зависит.

При подготовке PR не возвращать START_ROLE_1.md, `.env`, `.venv`, var/ и SQLite. U1 уже в общей main; новый diff U3 содержит только его серверный срез и обновления документации. Прежние отчёты клона сохранены в архиве, свежие результаты основной рабочей папки добавляются в ExecPlan. Для перехода между ветками сначала выполнить git status: при текущих незакоммиченных правках не запускать switch main/pull вслепую.
