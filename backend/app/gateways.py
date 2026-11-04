"""Точки подключения адаптеров. В этом этапе сетевых реализаций нет."""

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, NoReturn, Protocol, TypeVar

from pydantic import BaseModel, Field, JsonValue

from .config import Settings
from .schemas import Contract, Fragment, Health, ModelAvailability, RecordingMeta, Sha256, Text

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class TextRequest(Contract):
    operation: Literal["extract_needs", "assign_topic"]
    prompt: Text
    prompt_version: Text
    data: dict[str, JsonValue]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class GatewayResult(Contract, Generic[ResponseT]):
    value: ResponseT
    metadata: RecordingMeta


@dataclass(frozen=True)
class ApprovedAsset:
    """Создаёт сервер после проверки участником 1, не клиентский JSON."""

    asset_id: str
    absolute_path: Path
    content_hash: Sha256
    mime_type: str


class MediaText(Contract):
    fragments: list[Fragment]


class TextGateway(Protocol):
    def generate(self, request: TextRequest, response_schema: type[ResponseT]) -> GatewayResult[ResponseT]:
        """Адаптер обязан проверить ответ по response_schema до возврата."""
        ...


class AsrGateway(Protocol):
    def transcribe(self, asset: ApprovedAsset) -> GatewayResult[MediaText]: ...


class VisionGateway(Protocol):
    def describe(self, asset: ApprovedAsset) -> GatewayResult[MediaText]: ...


class GatewayUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def model_health(settings: Settings) -> Health:
    models = {}
    for kind, url, model_id in (
        ("text", settings.text_model_base_url, settings.text_model_id),
        ("asr", settings.asr_base_url, settings.asr_model_id),
        ("vision", settings.vl_base_url, settings.vl_model_id),
    ):
        configured = bool(url.strip() and model_id.strip())
        if settings.app_model_mode == "replay":
            code = "REPLAY_NOT_IMPLEMENTED"
            message = "Воспроизведение ещё не подключено; записей нет. Тестовая карточка подготовлена вручную."
        elif configured:
            code = "MODEL_ADAPTER_NOT_IMPLEMENTED"
            message = "Адрес и ID заданы, но адаптер не реализован и доступность не проверена."
        else:
            code = "MODEL_NOT_CONFIGURED"
            message = "Модель не настроена. Укажите внутренний адрес и ID после проверки протокола."
        models[kind] = ModelAvailability(
            status="not_implemented" if settings.app_model_mode == "replay" or configured else "not_configured",
            configured=configured,
            code=code,
            message=message,
        )
    return Health(model_mode=settings.app_model_mode, available_models=models)


class UnavailableGateways:
    """Безопасная заглушка с явной ошибкой вместо выдуманного ответа."""

    def __init__(self, settings: Settings):
        self.health = model_health(settings)

    def _fail(self, kind: str) -> NoReturn:
        state = self.health.available_models[kind]
        raise GatewayUnavailable(state.code, state.message)

    def generate(self, request: TextRequest, response_schema: type[ResponseT]) -> GatewayResult[ResponseT]:
        self._fail("text")

    def transcribe(self, asset: ApprovedAsset) -> GatewayResult[MediaText]:
        self._fail("asr")

    def describe(self, asset: ApprovedAsset) -> GatewayResult[MediaText]:
        self._fail("vision")
