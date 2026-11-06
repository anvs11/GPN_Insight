"""Извлечение пользовательских потребностей из нормализованного документа."""

from pathlib import Path

from ..gateways import TextGateway, TextRequest
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
    """Извлекает потребности через общий интерфейс текстового шлюза."""

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

        # Стабильные need_id назначает Pipeline.
        # Текущая модельная схема требует это поле, поэтому используем
        # временный placeholder, который Pipeline затем перезапишет.
        needs = [
            need.model_copy(update={"need_id": "pending"})
            for need in result.value.needs
        ]

        return ExtractionResult(
            needs=needs,
            metadata=[result.metadata],
        )