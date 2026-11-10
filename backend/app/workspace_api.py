"""Мониторинг на согласованном снимке SQLite, без вызовов моделей."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from .api import _filters
from .assessment_schemas import Category, Priority
from .repositories import Repository, RepositoryError
from .public_views import public_source
from .replay import semantic_hash
from .workspace_schemas import WorkspaceCard, WorkspacePage

router = APIRouter(prefix="/api/v1")


def assessments(repo, source, need_ids=None):
    categories, priorities, origins = set(), set(), set()
    missing = source.analysis_pending
    result = repo.processing_result(source.ticket.source_id)
    missing |= result is None and source.requires_analysis
    for need in source.needs:
        if need_ids is not None and need.need_id not in need_ids:
            continue
        value = repo.assessment(need.need_id, result.analysis_digest) if result and not source.analysis_pending else None
        if value is None:
            missing = True
        else:
            categories.add(value.assessment.category)
            priorities.add(value.assessment.priority)
            origins.add(value.metadata.origin)
    return sorted(categories), sorted(priorities), sorted(origins), missing


def source_card(repo, source, topic_ids):
    safe = public_source(source)
    categories, priorities, origins, missing = assessments(repo, source)
    result = repo.processing_result(source.ticket.source_id)
    state = (result.state if result else "pending" if source.requires_analysis or source.analysis_pending else
             "failed" if source.document and source.document.errors else "complete" if source.document else "pending")
    title = (source.needs[0].statement if source.needs else safe.ticket.text.strip() or "Обращение " + source.ticket.source_id)[:160]
    card = WorkspaceCard(kind="source", id=source.ticket.source_id, version=source.source_version,
        title=title, summary=source.needs[0].scenario if source.needs else "", created_at=source.ticket.created_at.isoformat(),
        last_seen=source.ticket.created_at.isoformat(), status=state, ticket_count=1, material_count=len(source.ticket.assets),
        categories=categories, priorities=priorities, unassessed=missing, errors=len(source.document.errors) if source.document else 0,
        pending=source.requires_analysis or source.analysis_pending or source.manual_recheck_required,
        synthetic=source.synthetic, origins=origins or [source.data_origin], source_ids=[source.ticket.source_id], topic_ids=topic_ids)
    card.revision = semantic_hash({"card": card.model_dump(exclude={"revision"}), "result": result.revision_id if result else None})
    return card


def topic_card(repo, card):
    categories, priorities, origins, missing = set(), set(), set(), False
    for source in card.sources:
        c, p, o, m = assessments(repo, source, set(card.topic.need_ids))
        categories.update(c); priorities.update(p); origins.update(o); missing |= m
    value = WorkspaceCard(kind="topic", id=card.topic.topic_id, version=card.topic.version, title=card.topic.title[:160],
        summary=card.topic.passport.need_statement, created_at=card.stats.first_seen.isoformat() if card.stats.first_seen else None,
        last_seen=card.stats.last_seen.isoformat() if card.stats.last_seen else None, status=card.topic.status,
        owner=card.topic.owner, ticket_count=card.stats.ticket_count, categories=sorted(categories), priorities=sorted(priorities),
        unassessed=missing, errors=sum(len(s.document.errors) for s in card.sources if s.document),
        pending=bool(card.pending_source_ids), synthetic=card.synthetic, origins=sorted(origins) or [card.data_origin],
        source_ids=[s.ticket.source_id for s in card.sources], topic_ids=[card.topic.topic_id])
    results = [repo.processing_result(s.ticket.source_id) for s in card.sources]
    value.revision = semantic_hash({"card": value.model_dump(exclude={"revision"}), "results": [r.revision_id if r else None for r in results]})
    return value


@router.get("/monitoring/{kind}/{object_id}", response_model=WorkspaceCard)
def monitoring_card(kind: Literal["topic", "source"], object_id: str, request: Request):
    _filters(request, set())
    with request.app.state.repository._session() as db:
        repo = Repository(request.app.state.repository.path, _db=db)
        if kind == "topic":
            return topic_card(repo, repo.get_topic(object_id, request.app.state.settings.app_model_mode))
        links = [c.topic.topic_id for c in repo.list_topics(request.app.state.settings.app_model_mode, support_level="all")
                 if any(s.ticket.source_id == object_id for s in c.sources)]
        return source_card(repo, repo.get_source(object_id), links)


@router.get("/monitoring", response_model=WorkspacePage)
def monitoring(request: Request, view: Literal["topics", "sources"] = "topics", q: str = Query("", max_length=120),
               category: Category | None = None, priority: Priority | None = None,
               status: str = Query("", max_length=40), support: Literal["all", "single", "repeated"] = "all",
               date_from: date | None = None, date_to: date | None = None,
               offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=100)):
    _filters(request, {"view", "q", "category", "priority", "status", "support", "date_from", "date_to", "offset", "limit"})
    allowed = {"new", "needs_research", "linked", "deferred", "declined", "implemented", "merged"} if view == "topics" else {"pending", "complete", "partial", "failed", "empty"}
    if status and status not in allowed or date_from and date_to and date_from > date_to:
        raise RepositoryError("MONITORING_FILTER_INVALID", "Проверьте статус и порядок дат выбранного представления.", 422)
    mode = request.app.state.settings.app_model_mode
    with request.app.state.repository._session() as db:
        repo = Repository(request.app.state.repository.path, _db=db)
        cards = repo.list_topics(mode, support_level="all")
        if view == "topics":
            # merged показывается только при явном фильтре.
            if status == "merged":
                cards = repo.list_topics(mode, support_level="all", status="merged")
            items = [topic_card(repo, c) for c in cards]
        else:
            links = {}
            for card in cards:
                for source in card.sources:
                    links.setdefault(source.ticket.source_id, []).append(card.topic.topic_id)
            ids = db.execute("SELECT source_id FROM source_records WHERE product_id='webneft'").fetchall()
            items = [source_card(repo, repo.get_source(row[0]), links.get(row[0], [])) for row in ids]
        def matches(item):
            day = None
            if item.created_at:
                day = datetime.fromisoformat(item.created_at).astimezone(ZoneInfo("Europe/Moscow")).date()
            return (not q or q.casefold() in (item.title + " " + item.summary + " " + item.id).casefold()) and (
                not category or category in item.categories) and (not priority or priority in item.priorities) and (
                not status or item.status == status) and (support == "all" or view == "sources" or
                (item.ticket_count >= 2) == (support == "repeated")) and (not date_from or day and day >= date_from) and (not date_to or day and day <= date_to)
        items = sorted((i for i in items if matches(i)), key=lambda i: (datetime.fromisoformat(i.last_seen).timestamp() if i.last_seen else float('-inf'), i.id), reverse=True)
        return WorkspacePage(items=items[offset:offset + limit], total=len(items), offset=offset, limit=limit, model_mode=mode)
