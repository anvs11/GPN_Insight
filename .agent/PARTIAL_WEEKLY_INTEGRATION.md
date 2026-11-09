# Подключить partial processing, оценки, недельную сводку и media API

Этот ExecPlan ведётся по `.agent/PLANS.md` и дополняет общий `.agent/EXECPLAN.md`. Пользователь 09.11.2026 поручил A4–A7 в текущей feat/weekly-partial-media; словарь категории/приоритетов подтверждён через У2. Предыдущий срез A1–A3 и пользовательские документы сохраняются.

## Purpose / Big Picture

После этапа обычный сервер сможет анализировать пригодную часть зарегистрированного synthetic media источника, показывать ошибки вложений, дополнять результат при повторе без потери решений и выдавать недельную сводку за последнюю завершённую неделю Москвы. Оригинал synthetic файла доступен по его идентификаторам, а не клиентскому пути. Live не подключается. Оценка категории/приоритета имеет проверяемые evidence и origin; отсутствие оценки не становится normal.

## Progress

- [x] (2026-11-09) Прочитаны текущие интерфейсы/план/состояние Git. Снимок предыдущей работы и private SHA: var/reviews/partial-weekly-integration-20261109/before.json.
- [x] (2026-11-09) A4: SQLite v4 migration/backup/rollback, immutable results/исторические fragments, атомарное обновление needs/links; решения/паспорта/history/overrides сохраняются, retirement/manual recheck видимы.
- [x] (2026-11-09) A4: production partial bootstrap с явным media profile/recordings, один worker/два прохода, старые text hashes/профили сохранены. Реальный process interrupt/active repeat/restart/reuse проверен.
- [x] (2026-11-09) A5: NeedAssessment/AssessmentResult, classify-need-v1, exact metadata/evidence/replay, отдельное хранение/read/topic summary; unknown/missing не превращаются в normal, keyword rules нет. У2 extract/assign/propose не менялись.
- [x] (2026-11-09) A6: completed MSK week/UTC boundaries, HTTP/CLI, categories/priority/unknown/coverage/errors и предыдущая неделя из одного snapshot; scheduler/доставка не реализованы.
- [x] (2026-11-09) A7: synthetic binary GET/HEAD, safe reread/MIME/no-store/nosniff, audio ranges/416, nonsynthetic403/unknown404/integrity503; HTTP/restart/CLI пройдены. Frontend получил read types/validators и URL helpers; новые экраны не реализовывались за У4.
- [x] (2026-11-09) 743 backend passed, 4 symlink Windows skips, 1 прежний warning за57.47s; 260 frontend tests/build. Старые HTTP/CLI/import/workflow/restart и новые partial/week/media процедуры прошли. 4 новых contract samples, export54, прежние 16/7/proposal/14workflow и pip check прошли; CLI report --completed-week сохранил UTF-8 файл. Audit var/reviews/partial-weekly-integration-20261109/result.json: private5/legacy7/source/archive сохранены, importer/source_content_hash/extract-assign-propose/pins/UI components неизменны, diff/credential scan чисты, staged пуст. Результаты не подменяют качество модели.
- [ ] A7 browser-click приёмка: Computer Use остановил попытку из-за невозможности уверенно определить Windows browser URL. Не обходить ограничение и не заявлять клики проверенными. У4 подключает новые экраны и проводит визуальную приёмку.

## Context and Orientation

backend/app/pipeline.py содержит общий последовательный worker, backend/app/ingestion/integration.py — проверенный анализ У2, bootstrap.py — явную конфигурацию replay. Новый MediaNormalizer возвращает NormalizationResult с полной ошибкой/покрытием; analysis_input создаёт пригодный вход без стирания исходной ошибки. Legacy normalized_documents/stored_needs неизменны по старому API, поэтому новая публикация будет отдельной repository операцией, не ослаблением прежнего save_normalized_document. Исторические fragments нужны паспортам и ручным решениям, удалять их нельзя.

Revision — SHA полного результата; analysis_digest — SHA профиля и пригодных фрагментов. Новый digest требует extraction, неизменный digest переиспользует его. ID потребности строится по её содержанию и точной версии источника, поэтому неизменная потребность сохраняет назначение/override при добавлении другого материала. Исчезнувшие needs становятся неактивными; история не удаляется. Сомнительные ручные назначения явно требуют recheck.

## Milestones and Plan of Work

A4 добавляет историю результатов/активацию анализа отдельной транзакцией и сохраняет старые таблицы. Публикация нормализации может предшествовать извлечению: в этот момент прежний анализ виден как pending, а не как итог нового контекста. Извлечение и переключение активных needs атомарны; assignment идёт вторым проходом, его ошибка видна. Файлы не входят в SQLite-транзакцию. После retry/restart контрольные точки нормализации и анализа проверяются по точному профилю/digest.

A5 вводит отдельную оценку потребности и её revision/metadata/evidence, совместимую со старыми ExtractedNeed. Воспроизведение только точного известного входа; незнакомая запись — ошибка, никакой оценки по словам. Агрегация категории и приоритета считает distinct source_id; potentially_critical требует объяснения и неизвестностей, unknown остаётся unknown. Промпты существующего извлечения/группировки не переписываются.

A6 определяет границы последней завершённой недели Europe/Moscow независимо от даты запуска анализа. Источник отбирается по created_at. HTTP/CLI формируют отчёт по запросу; scheduler и внешняя доставка не поручены. В отчёте отдельно period и all-time, неоценённое/ошибки/неполное покрытие.

A7 выдаёт только synthetic registered файл с exact product/source/version/asset через resolver и повторную проверку bytes. MIME известен серверу, nosniff/no-store, один корректный byte range для аудио, никакого произвольного path/URL. Неподключённый UI media viewer не объявлять готовым; передать У4 точные команды/DTO.

## Concrete Steps and Validation

Из backend в PowerShell после реализации:

    ..\.venv\Scripts\python.exe -X utf8 -m pytest -q -rs
    ..\.venv\Scripts\python.exe -X utf8 -m app.cli validate-examples
    ..\.venv\Scripts\python.exe -m pip check

Новые targeted tests должны проверять rollback, backup/idempotent migration, partial→retry→reopen, digest checkpoint, отсутствие дублей, сохранённые решения/паспорт/override, evidence/metadata, неделю на UTC/MSK/смене года, точные ranges/integrity/403 для nonsynthetic. Ручная процедура использует новую synthetic БД в var; рабочую .env и SQLite не мигрирует. Старые text scripts — регрессия, frontend test/build при изменении DTO. Linux/macOS используют ../.venv/bin/python. Фактический итог и команды ручной процедуры записать после запуска, не выдавать ожидания за результаты.

## Idempotence and Recovery

Миграция в транзакции с backup существующей версии; повтор безопасен. Исторические revisions/fragments/needs/решения не удаляются. Неудачный extraction оставляет явное pending и прежнюю историю, не активирует пустой успех. Неудачная SQL-публикация откатывает SQLite, successful media cache остаётся для повтора. Активный запуск после рестарта interrupted. Никакого скрытого live/replay fallback. Commit/push отдельно не поручены.

## Interfaces and Dependencies

Сохранить importer/manifest/source_content_hash и pins. Использовать существующие ApprovedAsset/checked_open/resolve_registered_asset, MediaReplayGateway, AnalysisService, Repository, RunService. Новые поля HTTP аддитивны либо новый endpoint; frontend validators и примеры менять согласованно. Категория/приоритет отделены от need_type/Topic.status. Новых библиотек/моделей/брокеров нет.

## Surprises & Discoveries

Legacy total_materials суммирует 1+assets, тогда как success при старом текстовом пути увеличивался на источник. Новый partial профиль должен иметь явные material/source units и считать реальное число outcomes; поведение старых text checkpoint не переименовывается.

При retirement ручной связи недостаточно сохранить override в SQLite: topic.pending_source_ids и source.manual_recheck_required нужны для видимого состояния после исчезновения потребности. Historical fragments нельзя удалять при замене документа: они подтверждают прежние паспорта/историю. Publication отдельно проверяет зарегистрированные asset IDs/hashes/kinds; rollback-тест подтверждает, что ошибочная ревизия не меняет базу.

## Decision Log

09.11.2026, пользователь/У3: реализовать A4–A7 поверх A1–A3, У2 подтвердил согласование категории/критичности. Сохранить старые DTO extraction и text hashes; assessment — отдельный результат. Без готового live демонстрировать только явно authored/replay с видимыми ошибками.

## Outcomes & Retrospective

Серверные A4–A7 завершены. Фактический новый HTTP/restart/interrupt: var/partial-weekly-check-a8ae76647054478fa176aa4e3ed2ee31/result.json (обычный bootstrap; один тестовый барьер только в отдельном процессе прерывания). Старый text/CLI/scenario: var/server-completion-check-14c976b23be84b33ac200d0c78ee23b7/result.json; manual workflow: var/human-workflow-check-fa5d80b9f9de4e07a2aa7ccd9e2575b8/result.json; preview: var/preview-review-8cf8a1df10f04dd2b0ec56d1d5f4f61f/result.json. Full tests 743/260, четыре честных skips. Все данные synthetic/authored; no live. Следующая передача У4 — UI/визуальная приёмка, У2 — recordings/разметка/evaluation. Рабочие файлы и предыдущие документы защищены снимком; commit/push не поручены.

Обновление 09.11.2026: план создан перед изменением SQL/production pipeline по новому поручению пользователя.

Обновление 09.11.2026 после реализации: отмечены фактические A4–A7, команды/примеры и результаты, а browser-click выделен как ограничение средства. Причина: HTTP/процессные проверки не подтверждают визуальную приёмку новых экранов У4.
