from pathlib import Path

from app.analysis.extractor import NeedExtractor
from app.gateways import GatewayResult, TextRequest
from app.replay import semantic_hash
from app.schemas import (
    ExtractedNeed,
    ExtractedNeeds,
    Fragment,
    NormalizedDocument,
    ProcessingProfile,
    RecordingMeta,
)


class FakeTextGateway:
    """Тестовый шлюз: не обращается к сети и возвращает заданный ответ."""

    def __init__(self, response: ExtractedNeeds) -> None:
        self.response = response
        self.request = None
        self.response_schema = None
        self.metadata = RecordingMeta(
            request_hash="a" * 64,
            prompt_hash="b" * 64,
            normalization_hash="c" * 64,
            model_id="test-model",
            parameters={},
            origin="authored",
            recording_set_version="test-set",
        )

    def generate(self, request: TextRequest, response_schema):
        self.request = request
        self.response_schema = response_schema
        return GatewayResult(
            value=self.response,
            metadata=self.metadata,
        )


def make_profile() -> ProcessingProfile:
    return ProcessingProfile(
        model_id="test-model",
        normalization_version="test-normalization",
        prompt_hashes={
            "extract_needs": "b" * 64,
            "assign_topic": "d" * 64,
            "propose_topic": "c" * 64,
        },
        parameters={},
        recording_set_version="test-set",
    )


def make_document() -> NormalizedDocument:
    return NormalizedDocument(
        product_id="webneft",
        source_id="source-1",
        content_hash=semantic_hash("test document"),
        version=1,
        normalization_version="test-normalization",
        fragments=[
            Fragment(
                fragment_id="fragment-1",
                kind="text",
                text="Не получается войти в личный кабинет.",
            )
        ],
    )


def make_need() -> ExtractedNeed:
    return ExtractedNeed(
        need_id="temporary-id",
        source_id="source-1",
        fragment_id="fragment-1",
        evidence_quote="Не получается войти в личный кабинет.",
        scenario="Вход пользователя в личный кабинет",
        need_type="problem",
        statement="Пользователь не может войти в личный кабинет.",
        explicit_request=False,
    )


def test_extract_sends_document_to_gateway(tmp_path: Path):
    prompt_path = tmp_path / "extract.md"
    prompt_path.write_text("Тестовый промпт", encoding="utf-8")

    gateway = FakeTextGateway(
        ExtractedNeeds(needs=[make_need()])
    )
    extractor = NeedExtractor(
        gateway=gateway,
        profile=make_profile(),
        prompt_path=prompt_path,
    )
    document = make_document()

    result = extractor.extract(document)

    assert gateway.request is not None
    assert gateway.request.operation == "extract_needs"
    assert gateway.request.prompt == "Тестовый промпт"
    assert gateway.request.prompt_version == "extract-needs-v1"
    assert gateway.request.data["document"] == document.model_dump(mode="json")
    assert gateway.response_schema is ExtractedNeeds

    assert len(result.needs) == 1
    assert result.needs[0].statement == (
        "Пользователь не может войти в личный кабинет."
    )
    assert result.needs[0].need_id == "pending"


def test_extract_returns_gateway_metadata(tmp_path: Path):
    prompt_path = tmp_path / "extract.md"
    prompt_path.write_text("Тестовый промпт", encoding="utf-8")

    gateway = FakeTextGateway(
        ExtractedNeeds(needs=[make_need()])
    )
    result = NeedExtractor(
        gateway=gateway,
        profile=make_profile(),
        prompt_path=prompt_path,
    ).extract(make_document())

    assert result.metadata == [gateway.metadata]


def test_extract_supports_empty_result(tmp_path: Path):
    prompt_path = tmp_path / "extract.md"
    prompt_path.write_text("Тестовый промпт", encoding="utf-8")

    gateway = FakeTextGateway(ExtractedNeeds(needs=[]))
    result = NeedExtractor(
        gateway=gateway,
        profile=make_profile(),
        prompt_path=prompt_path,
    ).extract(make_document())

    assert result.needs == []
    assert result.metadata == [gateway.metadata]
    assert gateway.request is not None