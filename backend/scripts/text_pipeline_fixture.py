"""Явно authored-ответы только для подготовки синтетической проверки, без моделей."""

from datetime import datetime

from app.gateways import GatewayResult
from app.replay import make_record, stable_id
from app.schemas import (
    BatchAssignment, DatasetRegistration, DecisionUpdate, EvidenceRef, ExtractedNeed,
    ExtractedNeeds, PassportExample, TicketInput, Topic, TopicPassport,
)

SEED_SOURCE_ID = "SYNTHETIC-TEXT-INTEGRATION-SEED"
TOPIC_ID = "synthetic-contact-topic"


class AuthoredFixtureGateway:
    """Не live-gateway: выпускает только явно подготовленные authored-записи."""

    def __init__(self, profile):
        self.profile = profile
        self.records = []

    def generate(self, request, response_schema):
        if request.operation == "extract_needs":
            doc = request.data["document"]
            if doc["source_id"] not in {f"PREVIEW-T00{i}" for i in range(1, 5)}:
                raise AssertionError("Authored fixture supports only four known synthetic demo tickets")
            fragment = doc["fragments"][0]
            quote = fragment["text"].removeprefix("Синтетический пример. ")
            value = ExtractedNeeds(needs=[ExtractedNeed(need_id="pending", source_id=doc["source_id"],
                fragment_id=fragment["fragment_id"], evidence_quote=quote, scenario="Изменение контакта заявки",
                need_type="operational_request", statement="Изменить контактный телефон в оформленной заявке", explicit_request=True)])
        else:
            need = request.data["need"]
            topics = request.data["topics"]
            value = BatchAssignment(decision="match" if topics else "provisional_no_match",
                matched_topic_id=topics[0]["topic_id"] if topics else None,
                candidate_topic_ids=[topics[0]["topic_id"]] if topics else [],
                reason="Явно подготовленный синтетический ответ; модель не вызывалась.",
                evidence_refs=[EvidenceRef(source_id=need["source_id"], fragment_id=need["fragment_id"], quote=need["evidence_quote"])])
        record = make_record(request, response_schema, value.model_dump(mode="json"), model_id=self.profile.model_id,
            normalization_hash=self.profile.parameters["normalization_hash"],
            set_version=self.profile.recording_set_version, origin="authored")
        self.records.append(record)
        return GatewayResult(value=value, metadata=record.metadata)


def seed_existing_topic(repo, normalizer):
    """Отдельная authored-тема для warm-start; существующий seed-demo не меняет."""
    ticket = TicketInput(source_id=SEED_SOURCE_ID, created_at=datetime.fromisoformat("2026-09-20T12:00:00+03:00"),
        text="Синтетический пример. Изменить контактный телефон в оформленной заявке.")
    repo.register_dataset(DatasetRegistration(dataset_id="synthetic-text-integration-seed", synthetic=True,
        origin="authored", tickets=[ticket]))
    source = repo.get_source(ticket.source_id)
    local = normalizer.normalize_ticket(ticket, content_hash=source.content_hash, source_version=source.source_version)
    doc = local.model_copy(update={"fragments": [f.model_copy(update={"fragment_id": stable_id(
        "fragment", local.product_id, local.source_id, local.version, local.content_hash, i)}) for i, f in enumerate(local.fragments)]})
    quote = "Изменить контактный телефон в оформленной заявке."
    need = ExtractedNeed(need_id=stable_id("need", doc.product_id, doc.source_id, doc.version, doc.content_hash, 0),
        source_id=doc.source_id, fragment_id=doc.fragments[0].fragment_id, evidence_quote=quote,
        scenario="Изменение контакта заявки", need_type="operational_request", statement=quote, explicit_request=True)
    ref = EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=quote)
    with repo.transaction() as tx:
        tx.save_normalized_document(doc)
        tx.save_needs(doc.product_id, doc.source_id, doc.version, [need])
        tx.create_topic(Topic(topic_id=TOPIC_ID, title="Контакт в оформленной заявке", summary=quote,
            passport=TopicPassport(scenario=need.scenario, need_statement=need.statement,
                scope="Изменение контактного телефона в существующей заявке", scope_confirmed=True,
                inclusion_examples=[PassportExample(text=quote, evidence_refs=[ref])], version=1),
            need_ids=[need.need_id], evidence=[ref]), origin="authored", analysis_mode=None)
    card = repo.get_topic(TOPIC_ID, "replay")
    repo.record_decision(TOPIC_ID, DecisionUpdate(status="needs_research", owner="Синтетический владелец",
        reason="Ручное решение должно сохраниться", version=card.topic.version))
    return repo.get_topic(TOPIC_ID, "replay")
