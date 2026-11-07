from collections import Counter
from datetime import date, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .demo import DemoFixture
from .schemas import (
    CatalogSnapshot, DecisionEvent, DecisionUpdate, EvidenceRef, ExtractedNeed,
    ManualOverride, ModelMode, Topic, TopicChangeEvent, TopicPassport, TopicDetail, TopicStats, TopicPeriodStats,
)
from .storage import RepositoryError, StorageRepository, now


class Repository(StorageRepository):
    """Единая точка хранения; HTTP/CLI используют те же транзакции."""

    @staticmethod
    def _topic(db, topic_id):
        row = db.execute("SELECT * FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
        if row is None:
            raise RepositoryError("TOPIC_NOT_FOUND", "Тема не найдена. Проверьте идентификатор.", 404)
        topic = Topic.model_validate_json(row["body_json"])
        needs = [ExtractedNeed.model_validate_json(r["body_json"]) for r in Repository._active_needs(db, topic_id)]
        return row, topic.model_copy(update={"need_ids": [n.need_id for n in needs], "evidence": [EvidenceRef(source_id=n.source_id, fragment_id=n.fragment_id, quote=n.evidence_quote) for n in needs]})

    @staticmethod
    def _active_needs(db, topic_id):
        return db.execute(
            "SELECT n.* FROM stored_needs n JOIN topic_need_links l ON n.need_id=l.need_id "
            "JOIN source_records s ON s.product_id=n.product_id AND s.source_id=n.source_id AND s.current_version=n.source_version "
            "WHERE l.topic_id=? AND l.active=1 AND n.active=1 ORDER BY n.need_id", (topic_id,)
        ).fetchall()

    @staticmethod
    def _bump_catalog(db, product_id):
        db.execute("INSERT INTO catalog_state VALUES (?,1) ON CONFLICT(product_id) DO UPDATE SET revision=revision+1", (product_id,))

    @staticmethod
    def _change(db, before, after, kind, author, reason):
        event = TopicChangeEvent(event_id=str(uuid4()), topic_id=after.topic_id, kind=kind, author=author, reason=reason,
                                 occurred_at=now(), before=before.model_dump(mode="json"), after=after.model_dump(mode="json"))
        db.execute("INSERT INTO topic_changes VALUES (?,?,?)", (event.event_id, event.topic_id, event.model_dump_json()))

    def seed_demo(self, fixture: DemoFixture) -> bool:
        with self.transaction() as tx:
            db = tx._db
            row = db.execute("SELECT content_hash FROM demo_fixtures WHERE fixture_id=?", (fixture.fixture_id,)).fetchone()
            if row:
                if row[0] != fixture.content_hash():
                    raise RepositoryError("DEMO_FIXTURE_CHANGED", "Начальное состояние изменилось. Используйте отдельную базу; существующая сохранена.")
                return False
            if db.execute("SELECT 1 FROM source_records LIMIT 1").fetchone() or db.execute("SELECT 1 FROM topics LIMIT 1").fetchone():
                raise RepositoryError("DEMO_DATABASE_NOT_EMPTY", "Загрузка тестового состояния разрешена только в пустую базу либо как точный повтор.")
            documents = {d.source_id: d for d in fixture.documents}
            for ticket in fixture.sources:
                document = documents[ticket.source_id]
                key = (ticket.product_id, ticket.source_id)
                db.execute("INSERT INTO source_records VALUES (?,?,?,1,'authored')", (*key, document.version))
                db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,0)", (*key, document.version, document.content_hash, ticket.model_dump_json(), now().isoformat()))
                tx.save_source_analysis(document, [n for n in fixture.needs if n.source_id == ticket.source_id])
            tx.create_topic(fixture.topic)
            db.execute("INSERT INTO demo_fixtures VALUES (?,?)", (fixture.fixture_id, fixture.content_hash()))
            return True

    def create_topic(self, topic: Topic, *, synthetic: bool | None = None, origin: str = "authored", analysis_mode: ModelMode | None = None,
                     manual_author: str | None = None, manual_reason: str | None = None) -> None:
        topic = Topic.model_validate(topic.model_dump())
        manual = manual_author is not None or manual_reason is not None
        if manual and not (isinstance(manual_author, str) and manual_author.strip() and isinstance(manual_reason, str) and manual_reason.strip()):
            raise RepositoryError("MANUAL_REASON_REQUIRED", "Ручное создание темы требует автора и причины.", 422)
        if topic.status != "new" or topic.owner is not None or topic.version != 1 or origin not in {"authored", "model"}:
            raise RepositoryError("INVALID_INITIAL_TOPIC", "Тема создаётся новой с версией 1; решения сохраняются отдельно.", 422)
        if not topic.need_ids:
            raise RepositoryError("EVIDENCE_REQUIRED", "Нельзя создать тему без подтверждённых потребностей.", 422)
        with self._session(write=True) as db:
            existing = db.execute("SELECT body_json FROM topics WHERE topic_id=?", (topic.topic_id,)).fetchone()
            if existing:
                if Topic.model_validate_json(existing[0]) == topic:
                    return
                raise RepositoryError("TOPIC_EXISTS", "ID темы уже существует; сохранённые решения не перезаписываются.")
            refs = list(topic.evidence)
            for example in topic.passport.inclusion_examples + topic.passport.exclusion_examples:
                refs.extend(example.evidence_refs)
            self._validate_evidence(db, topic.passport.product_id, refs)
            allowed_refs = set()
            has_synthetic_sources = False
            for need_id in topic.need_ids:
                row = db.execute("SELECT * FROM stored_needs WHERE need_id=? AND active=1", (need_id,)).fetchone()
                if row is None or row["product_id"] != topic.passport.product_id:
                    raise RepositoryError("NEED_NOT_ACTIVE", "Потребность неактивна или относится к другому продукту.", 422)
                if db.execute("SELECT 1 FROM topic_need_links WHERE need_id=? AND active=1", (need_id,)).fetchone():
                    raise RepositoryError("NEED_ALREADY_ASSIGNED", "Потребность уже относится к теме.")
                if not manual and db.execute("SELECT 1 FROM manual_overrides WHERE need_id=? AND requires_recheck=0", (need_id,)).fetchone():
                    raise RepositoryError("MANUAL_OVERRIDE", "Перед созданием темы требуется разрешить ручное переопределение.")
                source = self._source(db, row["product_id"], row["source_id"], row["source_version"])
                has_synthetic_sources |= bool(source["synthetic"])
                need = ExtractedNeed.model_validate_json(row["body_json"])
                allowed_refs.add((need.source_id, need.fragment_id, need.evidence_quote))
            if {(r.source_id, r.fragment_id, r.quote) for r in topic.evidence} != allowed_refs:
                raise RepositoryError("INVALID_EVIDENCE", "Доказательства темы должны соответствовать её потребностям.", 422)
            if synthetic is not None and synthetic != has_synthetic_sources:
                raise RepositoryError("PROVENANCE_CONFLICT", "Признак синтетики темы должен соответствовать её источникам.", 422)
            db.execute("INSERT INTO topics(topic_id,body_json,product_id,synthetic,origin,analysis_mode) VALUES (?,?,?,?,?,?)", (topic.topic_id, topic.model_dump_json(), topic.passport.product_id, int(has_synthetic_sources), origin, analysis_mode))
            db.executemany("INSERT INTO topic_need_links VALUES (?,?,1)", [(topic.topic_id, n) for n in topic.need_ids])
            self._bump_catalog(db, topic.passport.product_id)
            if manual:
                tx = type(self)(self.path, _db=db)
                for need_id in topic.need_ids:
                    need = db.execute("SELECT * FROM stored_needs WHERE need_id=?", (need_id,)).fetchone()
                    _, current = self._topic(db, topic.topic_id)
                    tx.record_override(ManualOverride(override_id=str(uuid4()), product_id=topic.passport.product_id, source_id=need["source_id"],
                        source_version=need["source_version"], need_id=need_id, action="attach", topic_id=topic.topic_id,
                        author=manual_author, reason=manual_reason, created_at=now()), expected_versions={topic.topic_id: current.version})

    @staticmethod
    def _period(date_from, date_to):
        if date_from and date_to and date_from > date_to:
            raise RepositoryError("INVALID_PERIOD", "Начало периода позже окончания.", 422)

    def get_topic(self, topic_id: str, mode: ModelMode, *, date_from: date | None = None,
                  date_to: date | None = None) -> TopicDetail:
        self._period(date_from, date_to)
        with self.transaction() as tx:
            db = tx._db
            metadata, topic = self._topic(db, topic_id)
            if topic.status == "merged":
                topic = topic.model_copy(update={"canonical_topic_id": tx.resolve_topic_id(topic_id)})
            needs = [ExtractedNeed.model_validate_json(r["body_json"]) for r in self._active_needs(db, topic_id)]
            topic = topic.model_copy(update={"need_ids": [n.need_id for n in needs], "evidence": [EvidenceRef(source_id=n.source_id, fragment_id=n.fragment_id, quote=n.evidence_quote) for n in needs]})
            sources = [tx.get_source(s, topic.passport.product_id) for s in sorted({n.source_id for n in needs})]
            history = [DecisionEvent.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM decision_events WHERE topic_id=? ORDER BY topic_version", (topic_id,))]
            historical_synthetic = bool(db.execute(
                "SELECT coalesce(max(s.synthetic),0) FROM topic_need_links l JOIN stored_needs n ON n.need_id=l.need_id "
                "JOIN source_records s ON s.product_id=n.product_id AND s.source_id=n.source_id WHERE l.topic_id=?", (topic_id,)).fetchone()[0])
            pending = [r[0] for r in db.execute(
                "SELECT DISTINCT n.source_id FROM stored_needs n JOIN topic_need_links l ON l.need_id=n.need_id "
                "JOIN source_records r ON r.product_id=n.product_id AND r.source_id=n.source_id "
                "JOIN source_versions v ON v.product_id=r.product_id AND v.source_id=r.source_id AND v.version=r.current_version "
                "WHERE l.topic_id=? AND n.source_version<r.current_version AND v.needs_extracted=0 ORDER BY n.source_id", (topic_id,))]
        tickets = [s.ticket for s in sources]
        stats = self._stats(tickets, topic, history)
        period_stats = None
        if date_from is not None or date_to is not None:
            selected = [ticket for ticket in tickets
                        if (date_from is None or ticket.created_at.astimezone(ZoneInfo("Europe/Moscow")).date() >= date_from)
                        and (date_to is None or ticket.created_at.astimezone(ZoneInfo("Europe/Moscow")).date() <= date_to)]
            period_stats = TopicPeriodStats(**self._stats(selected, topic, history).model_dump(),
                                           date_from=date_from, date_to=date_to)
        return TopicDetail(topic=topic, stats=stats, sources=sources, history=history, model_mode=mode,
                           synthetic=bool(metadata["synthetic"]) or historical_synthetic or any(s.synthetic for s in sources),
                           data_origin=metadata["origin"], analysis_mode=metadata["analysis_mode"],
                           pending_source_ids=pending, period_stats=period_stats)

    @staticmethod
    def _stats(tickets, topic, history) -> TopicStats:
        dates = [t.created_at for t in tickets]
        weekly = Counter()
        for created_at in dates:
            local_date = created_at.astimezone(ZoneInfo("Europe/Moscow")).date()
            weekly[(local_date - timedelta(days=local_date.weekday())).isoformat()] += 1
        requester_ids = [t.requester_id for t in tickets]
        latest = history[-1] if history else None
        implemented = latest.implemented_at if latest and topic.status == "implemented" else None
        return TopicStats(
            ticket_count=len(tickets), requester_count=len(set(requester_ids)) if tickets and all(requester_ids) else None,
            weekly_counts=dict(sorted(weekly.items())), first_seen=min(dates) if dates else None, last_seen=max(dates) if dates else None,
            support_level="repeated" if len(tickets) >= 2 else "single",
            new_ticket_count_since_decision=sum(d > latest.occurred_at for d in dates) if latest else 0,
            post_implementation_ticket_count=sum(d > implemented for d in dates) if implemented else 0,
            review_due=bool(latest and topic.status == "deferred" and latest.review_after and latest.review_after <= now().astimezone(ZoneInfo("Europe/Moscow")).date()),
        )

    def list_topics(self, mode: ModelMode, *, product_id="webneft", support_level="repeated",
                    status=None, review_due=None, date_from=None, date_to=None) -> list[TopicDetail]:
        self._period(date_from, date_to)
        # Один snapshot: решение и счётчик не берутся из разных версий списка.
        with self.transaction() as tx:
            ids = [r[0] for r in tx._db.execute("SELECT topic_id FROM topics WHERE product_id=? ORDER BY topic_id", (product_id,))]
            cards = []
            for topic_id in ids:
                card = tx.get_topic(topic_id, mode, date_from=date_from, date_to=date_to)
                if status is None and card.topic.status == "merged":
                    continue
                if status is not None and card.topic.status != status:
                    continue
                if support_level not in {None, "all"} and card.stats.support_level != support_level:
                    continue
                if review_due is not None and card.stats.review_due != review_due:
                    continue
                if card.period_stats is not None and card.period_stats.ticket_count == 0:
                    continue
                cards.append(card)
            return cards

    def resolve_topic_id(self, topic_id: str) -> str:
        with self._session() as db:
            visited = set()
            while topic_id not in visited:
                visited.add(topic_id)
                _, topic = self._topic(db, topic_id)
                if topic.status != "merged":
                    return topic_id
                topic_id = topic.canonical_topic_id
            raise RepositoryError("CANONICAL_TOPIC_CYCLE", "Цепочка объединений повреждена. Проверьте историю тем.")

    def list_topic_ids(self, support_level: str | None = "repeated", product_id: str = "webneft") -> list[str]:
        with self._session() as db:
            topics = db.execute("SELECT topic_id,body_json FROM topics WHERE product_id=? ORDER BY topic_id", (product_id,)).fetchall()
            return [r[0] for r in topics if Topic.model_validate_json(r[1]).status != "merged" and
                    (support_level is None or (len({n["source_id"] for n in self._active_needs(db, r[0])}) >= 2) == (support_level == "repeated"))]

    def record_decision(self, topic_id: str, update: DecisionUpdate) -> None:
        with self._session(write=True) as db:
            _, topic = self._topic(db, topic_id)
            if topic.version != update.version:
                raise RepositoryError("VERSION_CONFLICT", "Тема уже изменена. Обновите карточку перед сохранением.")
            if topic.status == "merged":
                raise RepositoryError("TOPIC_MERGED", "Решение меняется в основной теме.")
            event = DecisionEvent(**update.model_dump(exclude={"version", "status"}), event_id=str(uuid4()), topic_id=topic_id,
                                  previous_status=topic.status, new_status=update.status, occurred_at=now(), topic_version=topic.version + 1)
            changed = topic.model_copy(update={"status": update.status, "owner": update.owner, "version": event.topic_version})
            db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (changed.model_dump_json(), topic_id))
            db.execute("INSERT INTO decision_events VALUES (?,?,?,?)", (event.event_id, topic_id, event.topic_version, event.model_dump_json()))

    def attach_needs(self, topic_id: str, need_ids: list[str], *, expected_version: int, author: str = "server", reason: str = "Новое свидетельство") -> None:
        with self._session(write=True) as db:
            _, topic = self._topic(db, topic_id)
            if type(expected_version) is not int or topic.version != expected_version or topic.status == "merged":
                raise RepositoryError("VERSION_CONFLICT", "Тема изменилась; перечитайте карточку.")
            changed = False
            for need_id in dict.fromkeys(need_ids):
                row = db.execute("SELECT * FROM stored_needs WHERE need_id=? AND active=1", (need_id,)).fetchone()
                if row is None or row["product_id"] != topic.passport.product_id:
                    raise RepositoryError("NEED_NOT_ACTIVE", "Потребность неактивна или относится к другому продукту.", 422)
                override = db.execute("SELECT body_json FROM manual_overrides WHERE need_id=? AND requires_recheck=0 ORDER BY rowid DESC LIMIT 1", (need_id,)).fetchone()
                if override:
                    manual = ManualOverride.model_validate_json(override[0])
                    if manual.action != "attach" or manual.topic_id != topic_id:
                        raise RepositoryError("MANUAL_OVERRIDE", "Автоматическое назначение запрещено ручным решением.")
                link = db.execute("SELECT topic_id FROM topic_need_links WHERE need_id=? AND active=1", (need_id,)).fetchone()
                if link:
                    if link[0] != topic_id:
                        raise RepositoryError("NEED_ALREADY_ASSIGNED", "Потребность уже относится к другой теме.")
                    continue
                db.execute("INSERT INTO topic_need_links VALUES (?,?,1) ON CONFLICT(topic_id,need_id) DO UPDATE SET active=1", (topic_id, need_id))
                changed = True
            if changed:
                _, current = self._topic(db, topic_id)
                after = current.model_copy(update={"version": topic.version + 1})
                db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (after.model_dump_json(), topic_id))
                self._change(db, topic, after, "attachment", author, reason)

    def update_passport(self, topic_id: str, passport: TopicPassport, *, expected_version: int, author: str, reason: str, title: str | None = None) -> None:
        passport = TopicPassport.model_validate(passport.model_dump())
        if not all(isinstance(value, str) and value.strip() for value in (author, reason)):
            raise RepositoryError("MANUAL_REASON_REQUIRED", "Изменение паспорта требует автора и причины.", 422)
        with self._session(write=True) as db:
            _, before = self._topic(db, topic_id)
            if before.status == "merged":
                raise RepositoryError("TOPIC_MERGED", "Паспорт меняется в основной теме.")
            if type(expected_version) is not int or before.version != expected_version or passport.version != before.passport.version + 1:
                raise RepositoryError("VERSION_CONFLICT", "Версия темы или паспорта изменилась.")
            if passport.product_id != before.passport.product_id:
                raise RepositoryError("PRODUCT_MISMATCH", "Продукт паспорта менять нельзя.", 422)
            self._validate_evidence(db, passport.product_id, [r for e in passport.inclusion_examples + passport.exclusion_examples for r in e.evidence_refs])
            after = Topic.model_validate({**before.model_dump(), "passport": passport, "title": title if title is not None else before.title, "version": before.version + 1})
            # Содержимое паспорта теперь подтверждено/изменено человеком.
            db.execute("UPDATE topics SET body_json=?,origin='authored' WHERE topic_id=?", (after.model_dump_json(), topic_id))
            self._change(db, before, after, "passport", author, reason)
            self._bump_catalog(db, passport.product_id)

    def catalog_snapshot(self, product_id: str = "webneft") -> CatalogSnapshot:
        with self.transaction() as tx:
            row = tx._db.execute("SELECT revision FROM catalog_state WHERE product_id=?", (product_id,)).fetchone()
            topics = [tx.get_topic(t, "replay").topic for t in tx.list_topic_ids(None, product_id)]
            return CatalogSnapshot(product_id=product_id, catalog_revision=row[0] if row else 0, topics=topics)

    def record_override(self, override: ManualOverride, *, expected_versions: dict[str, int]) -> None:
        override = ManualOverride.model_validate(override.model_dump())
        with self._session(write=True) as db:
            old = db.execute("SELECT body_json FROM manual_overrides WHERE override_id=?", (override.override_id,)).fetchone()
            if old:
                if ManualOverride.model_validate_json(old[0]) == override:
                    return
                raise RepositoryError("OVERRIDE_ID_CONFLICT", "Ручное событие с этим ID уже существует.")
            source = self._source(db, override.product_id, override.source_id, override.source_version)
            need = db.execute("SELECT * FROM stored_needs WHERE need_id=? AND active=1", (override.need_id,)).fetchone()
            if source["current_version"] != override.source_version or need is None or (need["product_id"], need["source_id"], need["source_version"]) != (override.product_id, override.source_id, override.source_version):
                raise RepositoryError("SOURCE_VERSION_CONFLICT", "Ручное решение относится к устаревшему или другому источнику.")
            affected = {r[0] for r in db.execute("SELECT topic_id FROM topic_need_links WHERE need_id=? AND active=1", (override.need_id,))}
            if override.topic_id:
                _, target = self._topic(db, override.topic_id)
                if target.passport.product_id != override.product_id or target.status == "merged":
                    raise RepositoryError("PRODUCT_MISMATCH", "Ручное решение требует немерженную тему того же продукта.", 422)
                affected.add(override.topic_id)
            before_topics = {}
            for topic_id in affected:
                _, before = self._topic(db, topic_id)
                if type(expected_versions.get(topic_id)) is not int or expected_versions.get(topic_id) != before.version:
                    raise RepositoryError("VERSION_CONFLICT", "Одна из затронутых тем изменилась; перечитайте карточки.")
                before_topics[topic_id] = before
            db.execute("INSERT INTO manual_overrides VALUES (?,?,?,?,?,?,?,0,?)", (override.override_id, override.product_id, override.source_id, override.source_version, override.need_id, override.topic_id, override.action, override.model_dump_json()))
            if override.action in {"attach", "insufficient_data"}:
                db.execute("UPDATE topic_need_links SET active=0 WHERE need_id=?", (override.need_id,))
            if override.topic_id:
                db.execute("INSERT INTO topic_need_links VALUES (?,?,?) ON CONFLICT(topic_id,need_id) DO UPDATE SET active=excluded.active", (override.topic_id, override.need_id, int(override.action == "attach")))
            for topic_id in affected:
                before = before_topics[topic_id]
                _, current = self._topic(db, topic_id)
                after = current.model_copy(update={"version": before.version + 1})
                db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (after.model_dump_json(), topic_id))
                self._change(db, before, after, "manual_override", override.author, override.reason)

    def topic_changes(self, topic_id: str) -> list[TopicChangeEvent]:
        with self._session() as db:
            return [TopicChangeEvent.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM topic_changes WHERE topic_id=? ORDER BY rowid", (topic_id,))]
