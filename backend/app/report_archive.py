"""Append-only снимки отчётов, без изменения решений и исходных обращений."""

import json
from uuid import uuid4
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from .api import _filters
from .clock import now, fixed_clock
from .repositories import Repository, RepositoryError
from .replay import semantic_hash
from .report import _cell
from .weekly import build_weekly_report, completed_week
from .workspace_api import source_card, topic_card
from .workspace_schemas import ArchivePage, ReportDraft, ReportSnapshot

router = APIRouter(prefix="/api/v1/reports/archive")


def read_snapshot(raw):
    try:
        value = ReportSnapshot.model_validate_json(raw).model_dump(mode="json")
        payload = {k: v for k, v in value.items() if k not in {"report_id", "digest", "generated_at"}}
        if semantic_hash(payload) != value["digest"]:
            raise ValueError("Snapshot checksum changed")
        return value
    except (ValueError, TypeError):
        raise RepositoryError("REPORT_INTEGRITY_FAILED", "Снимок отчёта повреждён. Он сохранён для проверки; экспорт не подтверждён.", 503) from None


def save_snapshot(db, payload):
    payload = {"format_version": "report-snapshot-v1", "source_versions": {}, "evidence": [], "distinct_source_count": None, **payload}
    digest = semantic_hash(payload)
    old = db.execute("SELECT body_json FROM report_snapshots WHERE digest=?", (digest,)).fetchone()
    if old:
        return read_snapshot(old[0])
    result = ReportSnapshot.model_validate(dict(payload, report_id=str(uuid4()), digest=digest, generated_at=now())).model_dump(mode="json")
    body = json.dumps(result, ensure_ascii=False)
    if len(body.encode("utf-8")) > 2 * 1024 * 1024:
        raise RepositoryError("REPORT_TOO_LARGE", "Уменьшите подборку: отчёт превышает 2 МиБ.", 413)
    db.execute("INSERT INTO report_snapshots VALUES (?,?,?,?)", (result["report_id"], digest, result["generated_at"], body))
    return result


@router.get("", response_model=ArchivePage)
def archive(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    _filters(request, {"offset", "limit"})
    with request.app.state.repository._session() as db:
        total = db.execute("SELECT count(*) FROM report_snapshots").fetchone()[0]
        values = [read_snapshot(r[0]) for r in db.execute("SELECT body_json FROM report_snapshots ORDER BY created_at DESC,report_id LIMIT ? OFFSET ?", (limit, offset))]
        return dict(items=[{k: v[k] for k in ("report_id", "title", "kind", "generated_at", "model_mode", "period", "selection_count")} for v in values], total=total, offset=offset, limit=limit)


@router.get("/{report_id}", response_model=ReportSnapshot)
def archived_report(report_id: str, request: Request):
    _filters(request, set())
    with request.app.state.repository._session() as db:
        row = db.execute("SELECT body_json FROM report_snapshots WHERE report_id=?", (report_id,)).fetchone()
        if not row:
            raise RepositoryError("REPORT_NOT_FOUND", "Сохранённый отчёт не найден. Обновите архив.", 404)
        return read_snapshot(row[0])


@router.get("/{report_id}/pdf")
def archived_pdf(report_id: str, request: Request):
    from .report_pdf import render_pdf
    snapshot = archived_report(report_id, request)
    # Имя не берётся из недоверенного заголовка или URL.
    return Response(render_pdf(snapshot), media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="gpn-insight-report.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/weekly", response_model=ReportSnapshot)
def snapshot_week(request: Request):
    _filters(request, set())
    with fixed_clock(now()), request.app.state.repository.transaction() as repo:
        period = completed_week().model_dump(mode="json")
        text = build_weekly_report(repo, request.app.state.settings.app_model_mode)
        return save_snapshot(repo._db, dict(kind="weekly", title=f"Неделя {period['period']['date_from']} — {period['period']['date_to']}",
            summary="", comment="", selections=[], cards=[], selection_count=0, period=period,
            model_mode=request.app.state.settings.app_model_mode, markdown=text,
            limitations="Снимок на момент формирования. Replay/authored не измеряет качество живых моделей."))


@router.post("", response_model=ReportSnapshot)
def snapshot_selection(body: ReportDraft, request: Request):
    _filters(request, set())
    mode = request.app.state.settings.app_model_mode
    with request.app.state.repository.transaction() as repo:
        cards, evidence, source_versions = [], [], {}
        for item in body.selections:
            if item.kind == "topic":
                detail = repo.get_topic(item.id, mode)
                version = detail.topic.version
                card = topic_card(repo, detail)
                refs = [r.model_dump(mode="json") for r in detail.topic.evidence[:10]]
                sources = detail.sources
            else:
                source = repo.get_source(item.id)
                version = source.source_version
                links = [c.topic.topic_id for c in repo.list_topics(mode, support_level="all") if any(s.ticket.source_id == item.id for s in c.sources)]
                card = source_card(repo, source, links)
                refs = [dict(source_id=n.source_id, fragment_id=n.fragment_id, quote=n.evidence_quote) for n in source.needs[:10]]
                sources = [source]
            if version != item.version or card.revision != item.revision:
                raise RepositoryError("REPORT_SELECTION_STALE", "Объект подборки изменился. Обновите карточки и подтвердите актуальные версии.", 409)
            for source in sources:
                source_versions[source.ticket.source_id] = source.source_version
            cards.append(card.model_dump(mode="json")); evidence.append(refs)
        lines = ["# " + _cell(body.title), "", "Резюме автора: " + _cell(body.summary), "",
            "Комментарий автора: " + _cell(body.comment), "", f"Режим сервера: {mode}. Объектов в подборке: {len(cards)}; уникальных источников: {len(source_versions)}.",
            "Комментарии к отчёту не изменяют решения. Replay/authored не является оценкой качества живой модели."]
        for card, refs in zip(cards, evidence):
            lines.extend(["", "## " + _cell(card["title"]), _cell(card["summary"]),
                f"Тип: {card['kind']}; ID: {_cell(card['id'])}; версия: {card['version']}; статус: {_cell(card['status'])}.",
                "Категории: " + _cell(", ".join(card["categories"]) or "оценка отсутствует"),
                "Приоритеты: " + _cell(", ".join(card["priorities"]) or "оценка отсутствует"),
                f"Синтетические данные: {card['synthetic']}; происхождение: {_cell(', '.join(card['origins']))}; ошибок: {card['errors']}."])
            lines.extend(f"- {_cell(ref['source_id'])}/{_cell(ref['fragment_id'])}: {_cell(ref['quote'])}" for ref in refs)
        return save_snapshot(repo._db, dict(kind="selection", **body.model_dump(mode="json"), cards=cards,
            evidence=evidence, source_versions=source_versions, selection_count=len(cards), distinct_source_count=len(source_versions),
            period=None, model_mode=mode, markdown="\n".join(lines), limitations="Снимок выбранных версий; не корпоративный SLA или подтверждение ущерба."))
