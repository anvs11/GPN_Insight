"""Проверка authored media-контракта без моделей, SQLite и файлов uploads."""

from hashlib import sha256
import json
from pathlib import Path

from app.config import REPO_ROOT
from app.gateways import MediaText
from app.ingestion.normalizer import NORMALIZATION_HASH, NORMALIZATION_VERSION
from app.masking import mask_pii
from app.schemas import MaterialError, NormalizedDocument, RecordingMeta, RegisteredMaterial, SourceDetail, TicketInput
from app.storage import source_content_hash


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> None:
    contract = json.loads((REPO_ROOT / "data/contracts/media-analysis-handoff.json").read_text())
    require(contract["contract_version"] == "media-analysis-handoff-v1", "Неверная версия контракта")
    require(contract["purpose"] == "media_contract_shapes_only_not_runtime_profile_or_recording", "Неверное назначение")
    require(contract["synthetic"] is True and contract["origin"] == "authored"
        and contract["model_called"] is False and contract["storage_paths_are_illustrative"] is True,
        "Примеры должны явно отличаться от модельных результатов")
    require(contract["media_processing_profile"] is None and contract["media_recording_metadata"] is None,
        "Неизвестный media-профиль и recording должны оставаться null")
    require(contract["recording_meta_fields"] == list(RecordingMeta.model_fields), "RecordingMeta изменился")
    require(contract["current_text_normalization"] == {"version": NORMALIZATION_VERSION, "hash": NORMALIZATION_HASH},
        "Текстовый recipe изменился")
    root = (REPO_ROOT / "data/media-import").resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    tickets = {t.source_id: t for t in map(TicketInput.model_validate, manifest["tickets"])}
    seen: set[str] = set()
    formats: set[str] = set()
    for example in contract["examples"]:
        source = SourceDetail.model_validate(example["source"])
        material = RegisteredMaterial.model_validate(example["registered_material"])
        media = MediaText.model_validate(example["adapter_value_authored_example"])
        document = NormalizedDocument.model_validate(example["normalized_document_contract_example"])
        error = MaterialError.model_validate(example["current_pipeline_error"])
        ticket = source.ticket
        require(ticket.source_id not in seen and tickets.get(ticket.source_id) == ticket, "Источник расходится с manifest")
        seen.add(ticket.source_id)
        require(source.synthetic and source.data_origin == "imported" and source.document is None
            and not source.needs and source.requires_analysis and source.source_version == 1, "Неверный provenance источника")
        require(len(ticket.assets) == 1, "Пример ожидает один материал")
        asset = ticket.assets[0]
        fixture = Path(example["fixture_path"])
        require(not fixture.is_absolute() and ".." not in fixture.parts, "Недопустимый fixture path")
        path = (REPO_ROOT / fixture).resolve()
        require(path.parent == root and path.name == asset.path, "Fixture вне synthetic набора")
        content = path.read_bytes()
        require(material.asset_id == asset.asset_id and material.kind == asset.kind
            and material.mime_type == asset.mime_type and material.original_filename == path.name,
            "Связность материала нарушена")
        require(material.byte_size == len(content) and material.content_hash == sha256(content).hexdigest(), "Изменились bytes fixture")
        require(source.content_hash == source_content_hash(ticket, {material.asset_id: material}), "Неверный hash источника")
        require((document.product_id, document.source_id, document.version, document.content_hash)
            == (ticket.product_id, ticket.source_id, source.source_version, source.content_hash), "Неверный контекст документа")
        require(document.normalization_version == "media-contract-example-not-implemented" and not document.errors,
            "Authored пример не является runtime-нормализацией")
        require(len(media.fragments) == 1, "Неверный пример MediaText")
        fragment = media.fragments[0]
        require(fragment.asset_id == material.asset_id and fragment.kind == ("asr" if asset.kind == "audio" else "vision")
            and fragment.text == "" and fragment.warning is not None
            and fragment.start_ms is None and fragment.end_ms is None, "Не выдавать authored пример за распознавание")
        require(document.fragments[1:] == media.fragments and document.fragments[0].kind == "text"
            and document.fragments[0].text == mask_pii(ticket.text), "Неверный masked текст/фрагменты")
        require(error.source_id == ticket.source_id and error.asset_id is None and error.stage == "normalization"
            and error.code == "MEDIA_PIPELINE_NOT_IMPLEMENTED"
            and error.message == "Этап не завершён. Проверьте код ошибки и интеграцию; успешные шаги сохранены для повтора.", "Неверный текущий guard")
        formats.add(material.mime_type)
    require(seen == set(tickets) and len(contract["examples"]) == 4
        and formats == {"audio/wav", "audio/mpeg", "image/png", "image/jpeg"}, "Нужно четыре формата без пропусков и дублей")
    print("media-analysis-handoff-v1: 4 examples valid; hashes/source binding/provenance checked; runtime media profile=not configured; models_called=false")


if __name__ == "__main__":
    main()
