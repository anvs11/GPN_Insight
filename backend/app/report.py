"""Сводка сохранённого состояния, без исходных обращений и модельных чисел."""

import html
import re
from zoneinfo import ZoneInfo

from .repositories import Repository
from .errors import RepositoryError
from .schemas import ModelMode, Run, RunRequest
from .storage import now


def _cell(value) -> str:
    text = "нет данных" if value is None else str(value)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = html.escape(text, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", text)


def build_report(repository: Repository, mode: ModelMode, period: RunRequest) -> str:
    """Все сущности читаются из одного SQLite snapshot; не вызывает initialize."""
    if not repository.path.is_file():
        raise RepositoryError("REPORT_DATABASE_MISSING", "База не найдена. Сначала подготовьте отдельную базу.", 503)
    with repository._session() as db:
        tx = type(repository)(repository.path, _db=db)
        source_rows = tx._db.execute("SELECT source_id FROM source_records WHERE product_id=? ORDER BY source_id",
            (period.product_id,)).fetchall()
        sources = {row[0]: tx.get_source(row[0], period.product_id) for row in source_rows}
        selected = {}
        for source_id, source in sources.items():
            date = source.ticket.created_at.astimezone(ZoneInfo("Europe/Moscow")).date()
            if (period.date_from is None or date >= period.date_from) and (period.date_to is None or date <= period.date_to):
                selected[source_id] = source
        cards = [tx.get_topic(topic_id, mode) for topic_id in tx.list_topic_ids(None, period.product_id)]
        if period.date_from is not None or period.date_to is not None:
            cards = [card for card in cards if (set(s.ticket.source_id for s in card.sources) | set(card.pending_source_ids)) & selected.keys()]
        reviews = [review for review in tx.list_reviews() if any(
            tx._db.execute("SELECT 1 FROM stored_needs WHERE need_id=? AND product_id=?", (need_id, period.product_id)).fetchone()
            for need_id in review.need_ids)]
        if period.date_from is not None or period.date_to is not None:
            reviews = [review for review in reviews if any(tx._db.execute(
                "SELECT source_id FROM stored_needs WHERE need_id=?", (need_id,)).fetchone()[0] in selected for need_id in review.need_ids)]
        runs = [Run.model_validate_json(row[0]) for row in tx._db.execute(
            "SELECT body_json FROM run_records WHERE product_id=? ORDER BY rowid DESC LIMIT 10", (period.product_id,))]
        run_count = tx._db.execute("SELECT COUNT(*) FROM run_records WHERE product_id=?", (period.product_id,)).fetchone()[0]
        need_count = sum(len(source.needs) for source in selected.values())
        analyzed = sum(not source.requires_analysis for source in selected.values())
        linked = {source.ticket.source_id for card in cards for source in card.sources if source.ticket.source_id in selected}
        requester_ids = [source.ticket.requester_id for source in selected.values()]
        requesters = len(set(requester_ids)) if requester_ids and all(requester_ids) else None
        origins = sorted({card.data_origin for card in cards} | {origin for run in runs for origin in run.origins})
        has_synthetic = any(source.synthetic for source in selected.values()) or any(card.synthetic for card in cards)
        errors = [(source_id, error) for source_id, source in selected.items() if source.document for error in source.document.errors]
        lines = ["# GPN Insight — сводка", "", f"Снимок: {_cell(now().isoformat())}",
            f"Продукт: {_cell(period.product_id)}. Режим сервера: {_cell(mode)}.",
            f"Период created_at (Europe/Moscow, включительно): {_cell(period.date_from or 'без начала')} — {_cell(period.date_to or 'без конца')}.",
            "", "Синтетические данные присутствуют; это не статистика компании." if has_synthetic else "Синтетические данные в выбранных источниках/темах не отмечены.",
            f"Происхождение сохранённых тем/последних запусков: {_cell(', '.join(origins) or 'не зафиксировано')}.",
            "Replay/authored не подтверждает качество живой модели. Проверка live и экономический эффект этим отчётом не измеряются.",
            "", f"Уникальных источников в периоде: {len(selected)}.",
            f"Уникальных заявителей в периоде: {_cell(requesters)}.",
            f"Источников с завершённым извлечением: {analyzed}; ожидают анализа/содержат ошибки: {len(selected) - analyzed}.",
            f"Активных потребностей в периоде: {need_count}; источников в действующих темах: {len(linked)}.",
            "Источник считается один раз независимо от числа его потребностей и тем; суммы строк тем не являются общим числом обращений.",
            "", "## Действующие темы", "", "| ID | Заголовок | Статус | Владелец | Версия | Обращений в периоде | Всего активных | Заявителей в периоде | Происхождение |",
            "|---|---|---|---|---|---|---|---|---|"]
        signals = []
        for card in cards:
            in_period = [s for s in card.sources if s.ticket.source_id in selected]
            ids = [s.ticket.requester_id for s in in_period]
            count = len(set(ids)) if ids and all(ids) else None
            lines.append("| " + " | ".join(_cell(v) for v in (card.topic.topic_id, card.topic.title, card.topic.status,
                card.topic.owner, card.topic.version, len(in_period), card.stats.ticket_count, count, card.data_origin)) + " |")
            signals.append(f"Тема {_cell(card.topic.topic_id)}: pending={len(card.pending_source_ids)}; review_due={str(card.stats.review_due).lower()}; "
                f"после последнего решения={card.stats.new_ticket_count_since_decision}; после внедрения={card.stats.post_implementation_ticket_count}. "
                "Эти сигналы рассчитаны по всем активным источникам темы и сами не меняют решение.")
        if not cards:
            lines.append("Действующих тем в выбранном периоде нет. Это не подтверждает отсутствие потребностей.")
        lines.extend(["", *signals])
        lines.extend(["", "## Ручная проверка", "", f"Открытых assignment review в выбранном периоде: {len(reviews)}; требуют новой версии: {sum(r.requires_recheck for r in reviews)}."])
        for review in reviews:
            lines.append(f"- {_cell(review.review_id)}: version={review.version}; requires_recheck={str(review.requires_recheck).lower()}; потребностей={len(review.need_ids)}.")
        lines.extend(["", "## Ошибки подготовленных материалов", "", f"Сохранённых ошибок выбранных источников: {len(errors)}."])
        for source_id, error in errors:
            lines.append(f"- {_cell(source_id)}: {_cell(error.stage)} / {_cell(error.code)}.")
        lines.extend(["", "## Последние запуски продукта", "",
            f"Показаны последние {len(runs)} из {run_count}; запуски не фильтруются по периоду обращений.",
            "Покрытие run относится к подготовке материалов, не к качеству/полноте анализа. Ошибки extraction/assignment видны отдельно."])
        for run in runs:
            lines.append(f"- {_cell(run.run_id)}: mode={_cell(run.mode)}; state={_cell(run.state)}; origins={_cell(', '.join(run.origins) or 'не зафиксировано')}; "
                f"coverage={_cell(run.coverage)}; success={run.successful_materials}; failed={run.failed_materials}; errors={len(run.errors)}.")
            for error in run.errors:
                lines.append(f"  - {_cell(error.source_id)}: {_cell(error.stage)} / {_cell(error.code)}.")
            if run.failure_code:
                lines.append(f"  - Ошибка запуска: {_cell(run.failure_code)}.")
        lines.extend(["", "Исходные тексты обращений, цитаты, токены и параметры подключения в отчёт не включены.", ""])
        return "\n".join(lines)
