"""Ручной текстовый путь на новой синтетической SQLite/replay; не подключение к серверу."""

import copy
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from app.analysis.extractor import DEFAULT_PROMPT_PATH
from app.ingestion.integration import build_text_pipeline, build_text_profile, PROMPT_VERSIONS
from app.config import REPO_ROOT, Settings
from app.db import initialize
from app.gateways import GatewayUnavailable, TextRequest
from app.ingestion.normalizer import NORMALIZATION_HASH, TextNormalizer
from app.masking import mask_pii
from app.replay import ReplayGateway, make_record, publish_records, stable_id
from app.repositories import Repository, RepositoryError
from app.schemas import (
    DatasetRegistration, DecisionUpdate, EvidenceRef, ExtractedNeeds, MaterialInput, PassportExample,
    ProcessingProfile, TicketInput, Topic, TopicPassport,
)


def main() -> None:
    example = json.loads((REPO_ROOT / "data/contracts/normalization-handoff-v2.json").read_text(encoding="utf-8"))
    assert example["synthetic"] is True and example["origin"] == "authored"
    ticket = TicketInput.model_validate(example["ticket"])
    original = ticket.model_dump()
    profile = ProcessingProfile.model_validate(example["profile"])
    settings = Settings(_env_file=None)
    assert profile == build_text_profile(settings, model_id=profile.model_id,
        recording_set_version=profile.recording_set_version,
        parameters={k: v for k, v in profile.parameters.items() if k not in {"normalization_hash", "text_integration"}})
    normalizer = TextNormalizer(profile)
    # Независимые от golden-примера границы: срок действия/соседние реквизиты
    # не должны скрывать PAN, открытая скобка и тире допустимы рядом с ИНН.
    for raw, expected in [
        ("ИНН — 1234567890; ИНН (123456789012)", "ИНН — [ИНН]; ИНН ([ИНН])"),
        ("4111 1111 1111 1111 02/27", "[КАРТА] 02/27"),
        ("4111 1111 1111 1111 4000 0000 0000 0002", "[КАРТА] [КАРТА]"),
        ("40817 810 1 0000 0000000 123", "[СЧЁТ] 123"),
    ]:
        assert mask_pii(raw) == expected
    folder = REPO_ROOT / "var" / ("text-normalizer-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    repo = Repository(folder / "check.db")
    # Только подготовка отдельной проверки; сам Normalizer не делает init-db/регистрацию.
    initialize(repo.path)
    repo.register_dataset(DatasetRegistration(dataset_id="synthetic-normalization-check", synthetic=True, tickets=[ticket]))
    source = repo.get_source(ticket.source_id)

    def snapshot():
        with closing(sqlite3.connect(repo.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            return tuple(db.iterdump())

    before = snapshot()
    local = normalizer.normalize_ticket(ticket, content_hash=source.content_hash, source_version=source.source_version)
    assert not local.errors and ticket.model_dump() == original and snapshot() == before
    assert (local.source_id, local.product_id, local.version, local.content_hash) == (
        ticket.source_id, ticket.product_id, source.source_version, source.content_hash)
    for marker in ["[EMAIL]", "[ТЕЛЕФОН]", "[КАРТА]", "[ИНН]", "[СЧЁТ]"]:
        assert marker in local.fragments[0].text
    assert "А123ВС77" in local.fragments[0].text and local.fragments[0].warning
    document = local.model_copy(update={"fragments": [f.model_copy(update={"fragment_id": stable_id(
        "fragment", local.product_id, local.source_id, local.version, local.content_hash, i)})
        for i, f in enumerate(local.fragments)]})
    assert local.fragments[0].fragment_id != document.fragments[0].fragment_id
    print("synthetic=true; source_origin=imported; replay_origin=authored")
    print("normalize: identity=preserved; original=unchanged; database=unchanged")
    print("mask: phone/email/card/inn/account=replaced; plate=preserved; warning=visible")
    print("mask-boundaries: inn-label/card-expiry/adjacent-financial=masked")
    payload = copy.deepcopy(example["extraction_response"])
    payload["needs"][0]["fragment_id"] = document.fragments[0].fragment_id
    request = TextRequest(operation="extract_needs", prompt=DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"), prompt_version=PROMPT_VERSIONS["extract_needs"],
        data={"document": document.model_dump(mode="json")}, parameters=profile.parameters)
    record = make_record(request, ExtractedNeeds, payload, model_id=profile.model_id, normalization_hash=NORMALIZATION_HASH,
        set_version=profile.recording_set_version, origin="authored")
    publish_records(folder / "recordings", [record])
    extractor = build_text_pipeline(ReplayGateway(folder / "recordings", profile.model_id, NORMALIZATION_HASH), profile, settings).analysis
    extraction = extractor.extract_needs(document)
    assert extraction.metadata == [record.metadata]
    print("extract: quote=masked-exact; metadata=authored; normalization_hash=matched")
    needs = [n.model_copy(update={"need_id": stable_id(
        "need", document.product_id, document.source_id, document.version, document.content_hash, i)})
        for i, n in enumerate(extraction.needs)]
    try:
        with repo.transaction() as tx:
            tx.save_normalized_document(document)
            raise RuntimeError("Synthetic interruption before needs publication")
    except RuntimeError:
        pass
    assert snapshot() == before and repo.get_source(ticket.source_id).document is None
    print("rollback: unpublished_document=absent; database=unchanged")
    with repo.transaction() as tx:
        tx.save_normalized_document(document)
        tx.save_needs(ticket.product_id, ticket.source_id, document.version, needs)
    need = needs[0]
    ref = EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)
    repo.create_topic(Topic(topic_id="synthetic-normalization-topic", title="Синтетическая связь", summary="Ручная проверка",
        passport=TopicPassport(scenario=need.scenario, need_statement=need.statement, scope="Только синтетический пример",
            inclusion_examples=[PassportExample(text=need.evidence_quote, evidence_refs=[ref])], version=1),
        need_ids=[need.need_id], evidence=[ref]))
    card = repo.get_topic("synthetic-normalization-topic", "replay")
    repo.record_decision(card.topic.topic_id, DecisionUpdate(status="needs_research", owner="Проверяющий",
        reason="Сохранить решение при повторе", version=card.topic.version))
    saved = repo.get_topic(card.topic.topic_id, "replay")
    before = snapshot()
    assert normalizer.normalize_ticket(ticket, content_hash=source.content_hash, source_version=source.source_version) == local
    with repo.transaction() as tx:
        tx.save_normalized_document(document)
        tx.save_needs(ticket.product_id, ticket.source_id, document.version, needs)
    assert snapshot() == before
    print("repeat: document=identical; decision/history/count=preserved")
    changed = document.model_copy(deep=True)
    changed.fragments[0].text += " Изменён опубликованный фрагмент."

    def rejected(label, action, code):
        try:
            action()
        except RepositoryError as exc:
            assert exc.code == code
        else:
            raise AssertionError("Published result was overwritten")
        assert snapshot() == before
        print(f"{label}: {code}; state_unchanged=true")

    rejected("normalization-conflict", lambda: repo.save_normalized_document(changed), "NORMALIZATION_CONFLICT")
    rejected("analysis-conflict", lambda: repo.save_needs(ticket.product_id, ticket.source_id, document.version,
        [need.model_copy(update={"statement": "Другая потребность"})]), "ANALYSIS_CONFLICT")
    media = ticket.model_copy(deep=True)
    media.assets.append(MaterialInput(asset_id="synthetic-audio", kind="audio", path="not-read.wav", mime_type="audio/wav"))
    failed = normalizer.normalize_ticket(media, content_hash=source.content_hash, source_version=source.source_version)
    assert failed.fragments == [] and failed.errors[0].code == "MEDIA_NORMALIZATION_NOT_SUPPORTED"
    try:
        extractor.extract_needs(failed)
    except GatewayUnavailable as exc:
        assert exc.code == "EXTRACTION_MATERIAL_ERRORS"
    else:
        raise AssertionError("Material error became empty success")
    print("media: MEDIA_NORMALIZATION_NOT_SUPPORTED; partial_text=false")
    legacy = profile.model_copy(deep=True)
    legacy.parameters["normalization_hash"] = "f" * 64
    rejected("profile-conflict", lambda: TextNormalizer(legacy), "NORMALIZATION_PROFILE_CONFLICT")
    reopened = Repository(repo.path)
    assert reopened.get_source(ticket.source_id).ticket == ticket
    assert reopened.get_source(ticket.source_id).document == document
    assert reopened.get_topic(saved.topic.topic_id, "replay") == saved and saved.stats.ticket_count == 1
    report = folder / "result.json"
    report.write_text(json.dumps({"synthetic": True, "source_origin": "imported", "replay_origin": "authored",
        "normalization_hash": NORMALIZATION_HASH, "source_identity_preserved": True, "original_preserved": True,
        "masking_boundary_checks": True,
        "normalizer_database_writes": False, "rollback": True, "decision_history_preserved": True,
        "ticket_count": 1, "conflicts": ["NORMALIZATION_CONFLICT", "ANALYSIS_CONFLICT"],
        "media_rejected_whole": True, "models_called": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: source/decision/history preserved after reopen; no models")
    print(f"report: {report.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
