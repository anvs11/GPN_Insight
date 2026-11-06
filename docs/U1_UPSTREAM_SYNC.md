# Сверка участника 1 с новым main

Адаптация завершена: два U1-патча применены в последующем этапе; U1-02/U1-03 перенесены, профиль/примеры/проверки обновлены, build_text_pipeline добавляет защиту стыка с настоящим AnalysisService. Ветка feat/u1-text-integration, 369 тестов и реальный API/strict authored replay прошли. Актуальные команды/границы — [TEXT_INTEGRATION.md](TEXT_INTEGRATION.md).

## Архив сверки до адаптации

Проверено 2026-11-06. GitHub: https://github.com/anvs11/GPN_Insight, PR #7 https://github.com/anvs11/GPN_Insight/pull/7, автор kirbystrov. PR слит; коммит участника 2 — 22beb7843c7680ae3f3ecc8724160328b9e68fc0, новый main — 2afa4311ec61a693c99cf30699192db60dff8a36. Прежняя локальная основа была 3a48ff0f0881c233f9333f87c0d5abd843973a07. Между основами два коммита, 14 добавленных файлов, 1 604 строки; общие DTO/storage/pipeline/main и зависимости не менялись.

## Как сохранена работа

Исходная рабочая копия остаётся в прежней папке и ветке docs/participant-2-start. Незакоммиченные код, данные и тесты не перемещались и не сбрасывались. Снимок 30 изменённых/новых файлов, staged/unstaged patch, Git-статус и SHA-256 сохранены до работы в var/backups/pre-github-sync-20b27cfe16 исходной копии. Локальные базы, .env, .venv и replay не переносились.

Новый независимый Git-клон расположен в var/checkouts/gpn-current относительно исходной папки. У него собственный .git и origin=https://github.com/anvs11/GPN_Insight.git. От актуального чистого main создана ветка docs/u1-sync; её изменения относятся только к плану/документации. Исходники участника 2 оставлены ровно из main. Коммит, push и merge не выполнялись. Клон находится внутри игнорируемого var исходного репозитория: его Git-команды нужно выполнять из корня самого клона, открыв этот каталог отдельным проектом в IDE.

## Что действительно сделал участник 2

| Файлы нового main | Фактическое поведение |
|---|---|
| app/analysis/extractor.py, prompts/extract.md | NeedExtractor.extract(NormalizedDocument) отправляет документ через TextGateway в схеме ExtractedNeeds, возвращает metadata, заменяет временные need_id на pending |
| app/analysis/service.py | AnalysisService реализует оба метода Analysis: extract_needs и assign_topic |
| app/adapters/text.py | build_analysis(gateway, profile, settings, *, mode="replay") создаёт AnalysisService; это сборка компонента, не сетевой live-адаптер Qwen |
| app/retrieval.py | Детерминированное лексическое ранжирование, top-k, признак неполного каталога |
| app/analysis/grouping.py | Разбиение кандидатов на пакеты и признаки неполного покрытия |
| app/analysis/service.py, prompts/assign.md | Назначение match/review; проверка ID текущего пакета, точных evidence_refs и версий просмотренных паспортов, неоднозначность/неполнота ведут в review |
| app/review.py | Чистые helper-функции review assessment/auto-accept; сами не сохраняют review и не являются HTTP-обработчиком |
| app/evaluation.py | Вычисление classification accuracy/coverage и extraction precision/recall/F1 по точным нормализованным формулировкам; это не выполненная оценка живой модели |
| tests/test_analysis_components.py, test_analysis_extractor.py, test_analysis_service.py | 18 новых тестов; вместе с прежними 176 получены фактические 194 passed |

Появление AnalysisService снимает прежнее утверждение «реализации Analysis нет». Оно ещё не означает готовность полного сквозного сценария. assign_topic всегда возвращает new_topic=None; при пустом каталоге или provisional_no_match серверный итог review, автоматического new/ProposedTopic нет. main.py не создаёт ни AnalysisService, ни настоящий Normalizer, поэтому обычный сервер всё ещё INTEGRATION_NOT_READY. Условия приёмки трёх исходов и cold-start остаются открытыми.

## Что у участника 1 пока только локально

В новом main importer.py по-прежнему возвращает IMPORT_NOT_IMPLEMENTED для dry_run=False. Наши текстовая фиксация U1-02 и masking/TextNormalizer U1-03 не опубликованы в новом main. Это сохранённая работа, которую нужно переносить отдельными срезами; не создавать её заново и не объявлять потерянной. Четыре synthetic обращения data/demo/manifest.json и прежние data/contracts уже есть в main и не переписываются.

Локальные 397 passed и новые 194 passed относятся к разному составу файлов. Это не падение 203 тестов и не подтверждение переноса U1. Локальный NeedsExtractor и его промпт/тесты — другая реализация участника 2, не совпадающая со слитым NeedExtractor. Нельзя переносить старые app/analysis/, prompts/, retrieval/review/evaluation или заменять их поверх работы коллеги.

## Новый стык вместо старых предположений

| Область | Новая основа |
|---|---|
| Создание Analysis | build_analysis(gateway, profile, settings, mode=...) / AnalysisService(gateway, profile, settings, mode=...); не TextAnalysis(gateway, profile) |
| Публичные методы | AnalysisService.extract_needs(document), assign_topic(need, snapshot); внутренний NeedExtractor.extract(document) |
| Версии промптов | extract-needs-v1 и assign-topic-v1; SHA-256 считать от реально прочитанного UTF-8 текста соответствующего prompts/*.md, не от имени версии и не semantic_hash строки |
| Параметры TextRequest | Весь profile.parameters передаётся gateway. Старый локальный extractor передавал только parameters.model_parameters; старые ключи replay несовместимы |
| Нормализация | Контракт Normalizer и NormalizedDocument неизменен. Сохраняются text-pii-v1-proposal и semantic_hash(normalization_recipe); фактический hash masking-v2: 731c60015a270661e72a5a1d17c8c07bcce9306582fdec43ec3d226889ecf63a |
| Маркеры/цитаты | [ТЕЛЕФОН], [EMAIL], [КАРТА], [ИНН], [СЧЁТ]; дословная цитата должна входить именно в masked-фрагмент с ID pipeline |
| Replay | Нужна новая recording_set_version и точные запросы новой реализации с обоими реальными промптами, параметрами и normalization_hash. Старые записи не заменять и не переименовывать |

Normalization version/hash и создание TextNormalizer(profile) ранее согласованы через пользователя. Новые параметры анализа, settings, mode, лимиты и фабрику перед production-подключением сверить с участниками 2/3. Не считать предложенный ниже проверочный профиль согласованным live-профилем. Наличие build_analysis не создаёт нового gateway и не даёт доступа к живой модели.

## Проверенные ограничения для передачи участникам 2 и 3

На отдельной синтетической SQLite выполнен side-by-side probe: локальные masking.py/normalizer.py загружены только для проверки, read-only, с DTO нового клона; runtime-файлы клона не заменялись. Настоящий новый AnalysisService и строгий ReplayGateway прошли извлечение с masked-цитатой и назначение на пустом каталоге. Ответы явно authored, моделей не было. Это проверка стыка компонентов, не production-подключение и не полный запуск Pipeline/API.

Получены воспроизводимые ограничения:

1. NeedExtractor возвращает выдуманную evidence_quote без собственной проверки. Сохранение через Repository.save_needs отклоняет её с INVALID_EVIDENCE: барьер хранения работает, публикации ложной цитаты в probe не было. Участнику 2 вернуть проверку source/fragment/точной masked-цитаты до возврата ExtractionResult.
2. Документ с MaterialError дошёл до gateway и превратился в needs=[] с успешной metadata. Участникам 2/3 согласовать явную ошибку этапа/пропуск анализа ошибочного материала. Не переносить эту обязанность в Normalizer и не скрывать её ослаблением тестов.
3. build_batches оценивает сокращённый passport-text, а запрос отправляет полные Topic. В probe estimated_chars=138, фактический topics JSON=10 763 при max_chars=1 000. Участнику 2 считать реально отправляемый объём; владельцу gateway уточнить полный бюджет, включая prompt/need/schema/JSON. Одно число символов не подтверждает лимит токенов модели.
4. Cold-start фактически review/new_topic=null, не создание темы. Отдельно согласовать ProposedTopic/new или честно ограничить первый интеграционный показ каталогом существующих синтетических тем. Authored seed-demo не превращать в imported и не объявлять его результатом модели.

В новом extractor также нет прежней локальной проверки профиля/всей metadata и явной retry-политики. Согласовать эти обязанности с владельцами Analysis/TextGateway/pipeline до live; схему общих DTO и чужие исходники в этой задаче не менять. Перечисленные ограничения не мешают независимым текстовым import/normalizer/HTTP-задачам участника 1.

Локальный отчёт проверки: var/reviews/u1-upstream-sync/compatibility-result.json в новом клоне. Профиль там явно synthetic_probe/authored; в нём оба prompt_hash и действующий normalization_hash. Старые контрактные JSON/replay сохранены. Исходник probe.py находится рядом, принимает путь к исходной рабочей копии; это диагностический артефакт, не переносимый production-компонент.

## Подготовка переноса без пересечения с участником 2

В var/reviews/u1-upstream-sync нового клона подготовлены u1-import-text.patch (5 файлов) и u1-normalizer-core.patch (3 файла), оба прошли git apply --check на 2afa431 и **не применены**. Это локальные артефакты, не опубликованные файлы репозитория. Первый содержит importer + тесты/процедуры импорта и preview; второй — masking.py, normalizer.py и test_pii_masking.py. Комбинированный u1-text-core.patch также сохранён только для сравнения.

Старые test_text_normalizer.py, check_text_normalizer.py и normalization-handoff.json требуют отдельной адаптации к AnalysisService/новым промптам/parameters. Их нельзя слепо копировать: они импортируют локальный NeedsExtractor/PROMPT_HASH и проверяют гарантии, которых новый extractor пока не выполняет. Сохранить проверки идентичности/ошибок/конфликтов; проблемы владельцев вынести явно, а не подменять реальный Analysis тестовым или удалять неудобные проверки.

Рабочие запросы дальнейших маленьких этапов — [START_ROLE_1.md](../START_ROLE_1.md). Этот документ не поручает выполнить их автоматически.

## Фактическая проверка клона

На macOS/Python 3.13.0 из backend нового клона выполнено ../../../../.venv/bin/python -m pytest -q: 194 passed, одно прежнее предупреждение Starlette/AnyIO. Использован существующий интерпретатор исходной копии с теми же закреплёнными requirements.txt; .venv не копировалось и зависимости не обновлялись. В самостоятельном клоне создать своё окружение по README; команды установки не выдаются за выполненные.

Для повторения проверки именно в подготовленном вложенном клоне, из его backend:

    ../../../../.venv/bin/python -m pytest -q

Ожидание до переноса U1: 194 passed, 1 warning; время отличается. Будущее число после переноса фиксировать только после фактического прогона. Windows, живая Qwen и браузерный сквозной путь в этой сверке не проверялись.

Финальная сверка: GitHub main повторно совпал с 2afa431, исходные ветка/HEAD и весь код сохранены. Из 30 файлов исходного снимка изменились только явные указатели в README/START_ROLE_1/ExecPlan. Все 6 772 прежних файла данных/настроек совпали по SHA-256; код нового клона совпадает с main, оба U1 patch по-прежнему проходят apply --check без применения. 16 общих/7 HTTP-примеров и pip check также прошли. Отчёт сохранности — var/reviews/u1-upstream-sync/preservation-result.json.
