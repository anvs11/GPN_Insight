# Текстовая нормализация U1

Будущая media-интеграция: [существующие DTO, source/version/material и provenance](MEDIA_ANALYSIS_HANDOFF.md). Этот handoff не снимает текущие media-ограничения и не вводит новый runtime-профиль.

Граница после media import U1, 2026-11-07: HTTP/CLI теперь могут зарегистрировать проверенные медиа. TextNormalizer по-прежнему возвращает MEDIA_NORMALIZATION_NOT_SUPPORTED для ticket с assets; его recipe/hash/профиль и код не изменены. Импорт файла не является распознаванием; новый multimodal normalizer/ASR/VL и профиль потребуют отдельной согласованной задачи. Текущий импорт — [IMPORT_HTTP.md](IMPORT_HTTP.md).

Реализованы app/masking.py и app/ingestion/normalizer.py. Для совместной работы с текущим AnalysisService использовать [TEXT_INTEGRATION.md](TEXT_INTEGRATION.md) и build_text_pipeline: он проверяет ошибки документа/цитаты/metadata до публикации. Normalizer сам не вызывает анализ.

    normalize_ticket(ticket: TicketInput, *, content_hash: str, source_version: int) -> NormalizedDocument
    normalizer = TextNormalizer(profile)

product_id/source_id берутся из ticket; content_hash/source_version — ровно из аргументов pipeline. NormalizedDocument.version=source_version, normalization_version=text-pii-v1-proposal (согласованное имя). Исходный TicketInput не изменяется, хеш не пересчитывается по masked-тексту, БД не читается. Один текстовый Fragment имеет локальный ID text-1, kind=text, asset_id=null, без временных меток. Pipeline назначает окончательный детерминированный ID до извлечения и атомарно сохраняет документ/checkpoint.

Пробелы/переводы строк/Unicode и инструкции внутри обращения сохраняются как данные вне заменённых диапазонов. Пустой текст даёт один пустой фрагмент; это не результат извлечения потребностей. Нормализатор не пишет документы/needs/checkpoints, не открывает транзакции, не читает файлы и не вызывает модели. Опубликованные результаты/ручные решения не перезаписываются обходом NORMALIZATION_CONFLICT/ANALYSIS_CONFLICT.

| Класс | Правило пилота | Маркер |
|---|---|---|
| Телефон | Распространённые российские +7/7/8 и национальные 10 цифр с первым разрядом 3/4/8/9, пробелы/NBSP/скобки/дефисы/точки | [ТЕЛЕФОН] |
| Email | Простые local@domain, Unicode/локальные домены; не полный RFC parser | [EMAIL] |
| Карта | 13–19 ASCII-цифр с Luhn, слитно, группы 4 и последняя 1–4, 4-6-5/4-6-4/4-3-3-3 | [КАРТА] |
| ИНН | 10/12 слитных цифр близко к метке ИНН до/после, включая тире/скобку/двоеточие; без checksum ИНН | [ИНН] |
| Счёт | 20 ASCII-цифр, слитно или 4-4-4-4-4 / 5-5-5-5 / 5-3-1-4-7; без банковского checksum | [СЧЁТ] |

Госномера по умолчанию сохраняются. Имена и адреса свободного текста не гарантируются; предупреждение об этом сохраняется в Fragment.warning. Это не полное обезличивание: нестандартные международные/обфусцированные форматы, неоднозначные числа, не-ASCII цифры и сложные email могут давать пропуски/ложные совпадения. Допустимы синтетические или явно разрешённые данные; оригиналы намеренно остаются без изменений.

Приоритеты: email/помеченный ИНН, затем финансовые номера и телефоны. В числовом блоке проверяются целые группы (не части длинного номера), не более пяти групп/20 цифр в кандидате. Выбирается максимальное покрытие цифр, при равенстве больше цифр счетов/меньше замен/более ранний длинный кандидат. Поэтому срок действия рядом с картой или соседние реквизиты не скрывают номер. Неверная Luhn-карта не режется на телефоны. Повторное маскирование стабильно, текущие маркеры сохраняются.

MASKING_VERSION=masking-v2. NORMALIZATION_HASH=semantic_hash(normalization_recipe()) через общий app.replay helper. Recipe содержит реальные regex/маркеры/форматы/приоритеты, версии, лимит 1 МиБ UTF-8, правила whitespace/фрагментов/медиа и предупреждение. Текущий hash: 731c60015a270661e72a5a1d17c8c07bcce9306582fdec43ec3d226889ecf63a. TextNormalizer(profile) требует точного NORMALIZATION_VERSION/NORMALIZATION_HASH. Старые placeholder/hash предыдущих правил не подходят; старые записи не переписываются. Изменение правил требует новой fingerprint/проверенного профиля, изменение интеграционных правил — text_integration.version в профиле.

Некорректные TicketInput/context дают безопасные NORMALIZATION_INPUT_INVALID/CONTEXT_INVALID; несовместимый профиль — NORMALIZATION_PROFILE_INVALID/CONFLICT. При корректной идентичности ошибка обработки возвращает fragments=[] и MaterialError(stage=normalization) с исходными source/asset ID: NORMALIZATION_UTF8_INVALID, NORMALIZATION_TEXT_TOO_LARGE, NORMALIZATION_MASKING_FAILED, NORMALIZATION_PRODUCT_UNSUPPORTED. Любые вложения отклоняют весь ticket с MEDIA_NORMALIZATION_NOT_SUPPORTED для каждого asset_id, без частичного текста. Exception/source content в сообщениях не выводятся.

data/contracts/normalization-handoff.json явно synthetic/authored, содержит ticket/context/recipe/profile и документы до/после назначения ID pipeline. Ответ извлечения подготовлен вручную и помечен соответственно, не результат живой Qwen. Его профиль обновлён для новой интеграции/обоих реальных промптов; прежний локальный JSON и старые контрактные JSON нового main не переписывались.

Проверки: test_pii_masking.py и test_text_normalizer.py — 106 passed. Проверены UTF-8/лимит, исходник/context/version/provenance, exact masked-quote через настоящий AnalysisService/strict replay, отсутствие I/O/моделей, цельный отказ медиа, profile mismatch, rollback и конфликты сохранения без потери решения/истории. Ручной scripts.check_text_normalizer пройден на новой отдельной SQLite; отчёт var/text-normalizer-check-ae1750cbca904ae9849d75418488bff8/result.json. Полный текстовый Pipeline/API проверен отдельно по TEXT_INTEGRATION.md.
