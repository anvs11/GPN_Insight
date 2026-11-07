
"""Извлечение пользовательских потребностей из нормализованного документа."""

from pathlib import Path

from ..gateways import GatewayUnavailable, TextGateway, TextRequest
from ..schemas import (
    ExtractedNeeds,
    ExtractionResult,
    NormalizedDocument,
    ProcessingProfile,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "extract.md"
)


class NeedExtractor:
    """Извлекает потребности и проверяет их связь с исходным документом."""

    def __init__(
        self,
        gateway: TextGateway,
        profile: ProcessingProfile,
        prompt_path: Path | None = None,
    ) -> None:
        self.gateway = gateway
        self.profile = profile
        self.prompt_path = prompt_path or DEFAULT_PROMPT_PATH

    def extract(self, document: NormalizedDocument) -> ExtractionResult:
        prompt = self.prompt_path.read_text(encoding="utf-8")

        request = TextRequest(
            operation="extract_needs",
            prompt=prompt,
            prompt_version="extract-needs-v1",
            data={
                "document": document.model_dump(mode="json"),
            },
            parameters=self.profile.parameters,
        )

        result = self.gateway.generate(request, ExtractedNeeds)

        # Проверяем, что каждая потребность ссылается на реальные
        # фрагменты именно переданного нормализованного документа.
        fragments = {
            fragment.fragment_id: fragment
            for fragment in document.fragments
        }

        for need in result.value.needs:
            if need.source_id != document.source_id:
                raise GatewayUnavailable(
                    "EXTRACTION_REFERENCE_INVALID",
                    "Потребность ссылается на другой source_id.",
                )

            fragment = fragments.get(need.fragment_id)
            if fragment is None:
                raise GatewayUnavailable(
                    "EXTRACTION_REFERENCE_INVALID",
                    "Потребность ссылается на неизвестный fragment_id.",
                )

            if (
                not need.evidence_quote
                or need.evidence_quote not in fragment.text
            ):
                raise GatewayUnavailable(
                    "EXTRACTION_QUOTE_INVALID",
                    "Цитата потребности отсутствует в указанном фрагменте.",
                )

        # Стабильные need_id назначает Pipeline.
        # До этого используем временный placeholder.
        needs = [
            need.model_copy(update={"need_id": "pending"})
            for need in result.value.needs
        ]

        return ExtractionResult(
            needs=needs,
            metadata=[result.metadata],
        )
