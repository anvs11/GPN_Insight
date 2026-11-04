from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .db import connection
from .demo import DemoFixture
from .schemas import (
    DecisionEvent, DecisionUpdate, ExtractedNeed, ModelMode, NormalizedDocument,
    SourceDetail, TicketInput, Topic, TopicDetail, TopicStats,
)


class RepositoryError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class Repository:
    """Хранение тестового пути; API импорта/группировки добавляются их владельцами."""

    def __init__(self, path: Path):
        self.path = path

    def seed_demo(self, fixture: DemoFixture) -> bool:
        """Атомарная однократная загрузка. Повтор не перезаписывает решения."""
        with connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT content_hash FROM demo_fixtures WHERE fixture_id=?", (fixture.fixture_id,)).fetchone()
            if row:
                if row[0] != fixture.content_hash():
                    raise RepositoryError("DEMO_FIXTURE_CHANGED", "Начальное состояние изменилось. Используйте отдельную базу; существующая сохранена.", 409)
                return False
            if db.execute("SELECT 1 FROM sources LIMIT 1").fetchone() or db.execute("SELECT 1 FROM topics LIMIT 1").fetchone():
                raise RepositoryError("DEMO_DATABASE_NOT_EMPTY", "Загрузка тестового состояния разрешена только в пустую базу либо как точный повтор.", 409)
            documents = {d.source_id: d for d in fixture.documents}
            for ticket in fixture.sources:
                db.execute("INSERT INTO sources VALUES (?, ?, ?)", (ticket.source_id, ticket.model_dump_json(), documents[ticket.source_id].model_dump_json()))
            for need in fixture.needs:
                db.execute("INSERT INTO needs VALUES (?, ?, ?)", (need.need_id, need.source_id, need.model_dump_json()))
            db.execute("INSERT INTO topics VALUES (?, ?)", (fixture.topic.topic_id, fixture.topic.model_dump_json()))
            db.executemany("INSERT INTO topic_needs VALUES (?, ?)", [(fixture.topic.topic_id, n) for n in fixture.topic.need_ids])
            db.execute("INSERT INTO demo_fixtures VALUES (?, ?)", (fixture.fixture_id, fixture.content_hash()))
            return True

    def get_source(self, source_id: str) -> SourceDetail:
        with connection(self.path) as db:
            row = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if row is None:
                raise RepositoryError("SOURCE_NOT_FOUND", "Источник не найден. Проверьте идентификатор.", 404)
            needs = db.execute("SELECT body_json FROM needs WHERE source_id=? ORDER BY need_id", (source_id,)).fetchall()
        return SourceDetail(
            ticket=TicketInput.model_validate_json(row["ticket_json"]),
            document=NormalizedDocument.model_validate_json(row["document_json"]),
            needs=[ExtractedNeed.model_validate_json(n[0]) for n in needs],
        )

    def get_topic(self, topic_id: str, mode: ModelMode) -> TopicDetail:
        # Все части карточки читаются в одном снимке SQLite.
        with connection(self.path) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT body_json FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
            if row is None:
                raise RepositoryError("TOPIC_NOT_FOUND", "Тема не найдена. Проверьте идентификатор.", 404)
            topic = Topic.model_validate_json(row[0])
            source_rows = db.execute(
                "SELECT DISTINCT s.* FROM sources s JOIN needs n ON n.source_id=s.source_id "
                "JOIN topic_needs tn ON tn.need_id=n.need_id WHERE tn.topic_id=? ORDER BY s.source_id", (topic_id,)
            ).fetchall()
            need_rows = db.execute(
                "SELECT n.body_json FROM needs n JOIN topic_needs tn ON tn.need_id=n.need_id "
                "WHERE tn.topic_id=? ORDER BY n.need_id", (topic_id,)
            ).fetchall()
            history = [DecisionEvent.model_validate_json(r[0]) for r in db.execute(
                "SELECT body_json FROM decision_events WHERE topic_id=? ORDER BY topic_version", (topic_id,)
            )]
        active_needs = [ExtractedNeed.model_validate_json(r[0]) for r in need_rows]
        sources = [SourceDetail(
            ticket=TicketInput.model_validate_json(r["ticket_json"]),
            document=NormalizedDocument.model_validate_json(r["document_json"]),
            needs=[n for n in active_needs if n.source_id == r["source_id"]],
        ) for r in source_rows]
        tickets = [s.ticket for s in sources]
        dates = [t.created_at for t in tickets]
        weekly = Counter()
        for created_at in dates:
            local_date = created_at.astimezone(ZoneInfo("Europe/Moscow")).date()
            weekly[(local_date - timedelta(days=local_date.weekday())).isoformat()] += 1
        requester_ids = [t.requester_id for t in tickets]
        latest = history[-1] if history else None
        # Этап 1 сохраняет только needs_research; остальные сигналы ещё не подключены.
        stats = TopicStats(
            ticket_count=len(tickets),
            requester_count=len(set(requester_ids)) if tickets and all(requester_ids) else None,
            weekly_counts=dict(sorted(weekly.items())),
            first_seen=min(dates) if dates else None,
            last_seen=max(dates) if dates else None,
            support_level="repeated" if len(tickets) >= 2 else "single",
            new_ticket_count_since_decision=sum(d > latest.occurred_at for d in dates) if latest else 0,
            post_implementation_ticket_count=0,
            review_due=False,
        )
        return TopicDetail(topic=topic, stats=stats, sources=sources, history=history, model_mode=mode)

    def list_topic_ids(self, support_level: str | None = "repeated") -> list[str]:
        with connection(self.path) as db:
            rows = db.execute(
                "SELECT t.topic_id, count(DISTINCT n.source_id) AS count FROM topics t "
                "LEFT JOIN topic_needs tn ON t.topic_id=tn.topic_id LEFT JOIN needs n ON n.need_id=tn.need_id "
                "GROUP BY t.topic_id ORDER BY t.topic_id"
            ).fetchall()
        return [r[0] for r in rows if support_level is None or (r[1] >= 2) == (support_level == "repeated")]

    def record_decision(self, topic_id: str, update: DecisionUpdate) -> None:
        if update.status != "needs_research":
            raise RepositoryError("FEATURE_NOT_IMPLEMENTED", "На этапе 1 доступно только решение «Исследуется». Остальной жизненный цикл ещё не реализован.", 501)
        with connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body_json FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
            if row is None:
                raise RepositoryError("TOPIC_NOT_FOUND", "Тема не найдена. Проверьте идентификатор.", 404)
            topic = Topic.model_validate_json(row[0])
            if topic.version != update.version:
                raise RepositoryError("VERSION_CONFLICT", "Тема уже изменена. Обновите карточку перед сохранением.", 409)
            event = DecisionEvent(
                **update.model_dump(exclude={"version", "status"}),
                event_id=str(uuid4()), topic_id=topic_id,
                previous_status=topic.status, new_status=update.status,
                occurred_at=datetime.now(timezone.utc), topic_version=topic.version + 1,
            )
            topic = topic.model_copy(update={"status": update.status, "owner": update.owner, "version": event.topic_version})
            db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (topic.model_dump_json(), topic_id))
            db.execute("INSERT INTO decision_events VALUES (?, ?, ?, ?)", (event.event_id, topic_id, event.topic_version, event.model_dump_json()))
