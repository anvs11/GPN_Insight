
"""Явная сборка U1 Normalizer и анализа U2 с проверкой границы компонентов."""
import os
from copy import deepcopy
from hashlib import sha256
from json import JSONDecodeError
from pathlib import Path

from httpx import TimeoutException
from pydantic import BaseModel, ValidationError

from ..adapters.text import build_analysis
from ..analysis.extractor import DEFAULT_PROMPT_PATH
from ..analysis.service import DEFAULT_ASSIGN_PROMPT_PATH
from ..config import Settings
from ..gateways import GatewayResult, GatewayUnavailable, TextGateway, TextRequest
from ..pipeline import PipelineAdapters
from ..replay import RESPONSE_SCHEMAS, ReplayGateway, canonical, request_key
from ..schemas import (
    AssignmentResult,
    BatchAssignment,
    CatalogSnapshot,
    ExtractedNeed,
    ExtractionResult,
    NormalizedDocument,
    ProcessingProfile,
    RecordingMeta,
)
from .normalizer import NORMALIZATION_HASH, NORMALIZATION_VERSION, TextNormalizer

INTEGRATION_VERSION = "u2-text-integration-v3"

PROMPT_VERSIONS = {
    "classify_need": "classify-need-v1",
    "extract_needs": "extract-needs-v1",
    "assign_topic": "assign-topic-v1",
    "propose_topic": "propose-topic-v1",
}

DEFAULT_PROPOSE_PROMPT_PATH = (
    Path(os.environ["APP_PROPOSE_PROMPT_PATH"])
    if os.environ.get("APP_PROPOSE_PROMPT_PATH")
    else None
)

def _proposal_prompt_path() -> Path:
    # Один путь используется и для хеша, и для вызова AnalysisService.
    return DEFAULT_PROPOSE_PROMPT_PATH or DEFAULT_ASSIGN_PROMPT_PATH.with_name("propose.md")

def _prompts() -> dict[str, str]:
    prompts = {
        "extract_needs": DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"),
        "assign_topic": DEFAULT_ASSIGN_PROMPT_PATH.read_text(encoding="utf-8"),
    }

    propose_prompt_path = _proposal_prompt_path()

    if propose_prompt_path.is_file():
        prompts["propose_topic"] = propose_prompt_path.read_text(
            encoding="utf-8"
        )

    return prompts


def _policy(settings: Settings, max_attempts: int) -> dict:
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise GatewayUnavailable(
            "ANALYSIS_PROFILE_INVALID",
            "max_attempts должен быть целым от 1 до 3.",
        )
    return {
        "version": INTEGRATION_VERSION,
        "max_attempts": max_attempts,
        "retrieval_k": settings.effective_retrieval_k,
        "p_max": settings.app_p_max,
        "passport_batch_max_chars": settings.app_passport_batch_max_chars,
        "max_assign_batches": settings.app_max_assign_batches,
    }


def build_text_profile(
    settings: Settings,
    *,
    model_id: str,
    recording_set_version: str,
    parameters: dict | None = None,
    max_attempts: int = 2,
) -> ProcessingProfile:
    """Фиксирует реальные ресурсы/лимиты; не угадывает model_id и не вызывает модели."""
    if parameters is not None and not isinstance(parameters, dict):
        raise GatewayUnavailable(
            "ANALYSIS_PROFILE_INVALID",
            "Параметры модели должны быть JSON-объектом.",
        )

    values = deepcopy(parameters if parameters is not None else {})
    if "normalization_hash" in values or "text_integration" in values:
        raise GatewayUnavailable(
            "ANALYSIS_PROFILE_INVALID",
            "Служебные параметры профиля задаёт сборка, а не входные данные.",
        )

    values.update(
        normalization_hash=NORMALIZATION_HASH,
        text_integration=_policy(settings, max_attempts),
    )

    return ProcessingProfile(
        model_id=model_id,
        normalization_version=NORMALIZATION_VERSION,
        prompt_hashes={
            op: sha256(text.encode("utf-8")).hexdigest()
            for op, text in _prompts().items()
        },
        parameters=values,
        recording_set_version=recording_set_version,
    )


def _payload(value):
    return value.model_dump(warnings=False) if isinstance(value, BaseModel) else value


class _CheckedGateway:
    """Проверяет настоящий gateway; не формирует metadata за него и не меняет ответ."""

    def __init__(
        self,
        gateway: TextGateway,
        profile: ProcessingProfile,
        settings: Settings,
        prompts: dict[str, str],
    ):
        self.gateway = gateway
        self.profile = profile
        self.mode = settings.app_model_mode
        self.batch_limit = settings.app_passport_batch_max_chars
        self.attempts = profile.parameters["text_integration"]["max_attempts"]
        self.prompts = prompts

    def generate(self, request: TextRequest, response_schema) -> GatewayResult:
        request = TextRequest.model_validate(
            request.model_dump()
        ).model_copy(deep=True)

        op = request.operation
        expected_schema = RESPONSE_SCHEMAS.get(op)

        if expected_schema is None:
            raise GatewayUnavailable(
                "ANALYSIS_OPERATION_UNSUPPORTED",
                "Неподдерживаемая операция анализа.",
            )

        if (
            op not in self.prompts
            or response_schema is not expected_schema
            or request.prompt != self.prompts[op]
            or request.prompt_version != PROMPT_VERSIONS[op]
            or canonical(request.parameters)
            != canonical(self.profile.parameters)
        ):
            raise GatewayUnavailable(
                "ANALYSIS_REQUEST_CONFLICT",
                "Запрос анализа не совпал с зафиксированным профилем.",
            )

        if (
            op == "assign_topic"
            and len(canonical(request.data.get("topics", []))) > self.batch_limit
        ):
            raise GatewayUnavailable(
                "ANALYSIS_BATCH_TOO_LARGE",
                "JSON паспортов превышает лимит. Уменьшите пакет или размер тем.",
            )

        last_code = "ANALYSIS_RESPONSE_INVALID"

        for _ in range(self.attempts):
            try:
                result = self.gateway.generate(
                    request.model_copy(deep=True),
                    response_schema,
                )
            except (TimeoutError, TimeoutException):
                last_code = "ANALYSIS_TIMEOUT"
                continue
            except (ValidationError, JSONDecodeError):
                last_code = "ANALYSIS_RESPONSE_INVALID"
                continue
            except GatewayUnavailable as exc:
                if exc.code in {
                    "GATEWAY_TIMEOUT",
                    "GATEWAY_RESPONSE_INVALID",
                }:
                    last_code = (
                        "ANALYSIS_TIMEOUT"
                        if exc.code == "GATEWAY_TIMEOUT"
                        else "ANALYSIS_RESPONSE_INVALID"
                    )
                    continue

                raise GatewayUnavailable(
                    exc.code,
                    "Gateway отклонил анализ. Проверьте код ошибки и конфигурацию.",
                ) from None
            except Exception:
                raise GatewayUnavailable(
                    "ANALYSIS_GATEWAY_FAILED",
                    "Gateway не завершил анализ. Результат не принят.",
                ) from None

            if not isinstance(result, GatewayResult):
                raise GatewayUnavailable(
                    "ANALYSIS_GATEWAY_RESULT_INVALID",
                    "Gateway должен вернуть value и metadata по контракту.",
                )

            try:
                meta = RecordingMeta.model_validate(_payload(result.metadata))
                canonical(meta.model_dump()).encode("utf-8")
            except (AttributeError, TypeError, ValueError):
                raise GatewayUnavailable(
                    "ANALYSIS_PROVENANCE_INVALID",
                    "Gateway вернул некорректные metadata.",
                ) from None

            if (
                meta.model_id != self.profile.model_id
                or meta.prompt_hash != self.profile.prompt_hashes[op]
                or meta.normalization_hash != self.profile.parameters["normalization_hash"]
                or meta.recording_set_version
                != self.profile.recording_set_version
                or canonical(meta.parameters) != canonical(request.parameters)
                or meta.request_hash
                != request_key(
                    request,
                    response_schema,
                    self.profile.model_id,
                    self.profile.parameters["normalization_hash"],
                )
                or (self.mode == "live" and meta.origin != "model")
            ):
                raise GatewayUnavailable(
                    "ANALYSIS_PROVENANCE_CONFLICT",
                    "Metadata не соответствуют запросу, профилю или режиму.",
                )

            try:
                value = response_schema.model_validate(_payload(result.value))
                canonical(value.model_dump(mode="json")).encode("utf-8")
            except (AttributeError, TypeError, ValueError):
                last_code = "ANALYSIS_RESPONSE_INVALID"
                continue

            return GatewayResult(value=value, metadata=meta)

        raise GatewayUnavailable(
            last_code,
            "Анализ не завершён в пределах числа попыток. Проверьте gateway и повторите запуск.",
        ) from None


class _CheckedAnalysis:
    """Сохраняет алгоритмы U2; проверяет вход и результат на границе pipeline."""

    def __init__(self, service, profile: ProcessingProfile):
        self.service = service
        self.profile = profile

    def extract_needs(self, document: NormalizedDocument) -> ExtractionResult:
        try:
            if not isinstance(document, NormalizedDocument):
                raise ValueError("NormalizedDocument required")

            document = NormalizedDocument.model_validate(
                document.model_dump()
            ).model_copy(deep=True)

            canonical(document.model_dump(mode="json")).encode("utf-8")
        except (TypeError, ValueError):
            raise GatewayUnavailable(
                "EXTRACTION_INPUT_INVALID",
                "Передайте корректный нормализованный документ.",
            ) from None

        if document.errors:
            raise GatewayUnavailable(
                "EXTRACTION_MATERIAL_ERRORS",
                "Материал содержит ошибки; пустой успешный анализ не подтверждён.",
            )

        if (
            document.product_id != "webneft"
            or document.normalization_version
            != self.profile.normalization_version
        ):
            raise GatewayUnavailable(
                "NORMALIZATION_PROFILE_CONFLICT",
                "Продукт или версия нормализации не соответствуют профилю.",
            )

        result = ExtractionResult.model_validate(
            self.service.extract_needs(document).model_dump()
        )

        fragments = {f.fragment_id: f for f in document.fragments}

        for need in result.needs:
            if (
                need.source_id != document.source_id
                or need.fragment_id not in fragments
            ):
                raise GatewayUnavailable(
                    "EXTRACTION_REFERENCE_INVALID",
                    "Потребность ссылается на чужой источник или фрагмент.",
                )

            if need.evidence_quote not in fragments[need.fragment_id].text:
                raise GatewayUnavailable(
                    "EXTRACTION_QUOTE_INVALID",
                    "Цитата не входит дословно в указанный замаскированный фрагмент.",
                )

        return result

    def assign_topic(
        self,
        need: ExtractedNeed,
        topic_passports: CatalogSnapshot,
    ) -> AssignmentResult:
        try:
            need = ExtractedNeed.model_validate(
                _payload(need)
            ).model_copy(deep=True)

            topic_passports = CatalogSnapshot.model_validate(
                _payload(topic_passports)
            ).model_copy(deep=True)

            canonical({
                "need": need.model_dump(),
                "catalog": topic_passports.model_dump(),
            }).encode("utf-8")

            if topic_passports.product_id != "webneft":
                raise ValueError("Unsupported product")
        except (TypeError, ValueError):
            raise GatewayUnavailable(
                "ASSIGNMENT_INPUT_INVALID",
                "Передайте корректную потребность и каталог продукта webneft.",
            ) from None

        try:
            result = AssignmentResult.model_validate(
                self.service.assign_topic(need, topic_passports).model_dump()
            )
        except GatewayUnavailable:
            raise
        except (TypeError, ValueError):
            raise GatewayUnavailable(
                "ASSIGNMENT_RESPONSE_INVALID",
                "Назначение не соответствует исходной потребности или каталогу.",
            ) from None

        if not result.metadata:
            raise GatewayUnavailable(
                "ANALYSIS_PROVENANCE_MISSING",
                "Нет проверенных ответов назначения. Проверьте размеры паспортов и лимиты поиска.",
            )

        return result


def build_text_pipeline(
    gateway: TextGateway,
    profile: ProcessingProfile,
    settings: Settings,
) -> PipelineAdapters:
    """Готовые настоящие компоненты для create_app(..., pipeline_adapters=...)."""
    try:
        profile = ProcessingProfile.model_validate(
            profile.model_dump()
        ).model_copy(deep=True)

        settings = settings.model_copy(deep=True)
        canonical(profile.model_dump()).encode("utf-8")

        policy = profile.parameters["text_integration"]
        expected_policy = _policy(settings, policy["max_attempts"])

        if canonical(policy) != canonical(expected_policy):
            raise ValueError("Integration settings mismatch")
    except (AttributeError, KeyError, TypeError, ValueError):
        raise GatewayUnavailable(
            "ANALYSIS_PROFILE_INVALID",
            "Создайте профиль через build_text_profile с текущими настройками.",
        ) from None

    normalizer = TextNormalizer(profile)

    if settings.app_model_mode == "live" and isinstance(gateway, ReplayGateway):
        raise GatewayUnavailable(
            "ANALYSIS_MODE_CONFLICT",
            "ReplayGateway нельзя подключать как live-модель.",
        )

    prompts = _prompts()

    if (
        set(profile.prompt_hashes) != set(prompts)
        or any(
            profile.prompt_hashes[op]
            != sha256(text.encode("utf-8")).hexdigest()
            for op, text in prompts.items()
        )
    ):
        raise GatewayUnavailable(
            "ANALYSIS_PROFILE_CONFLICT",
            "Хеши промптов изменились. Нужны новый профиль и точные записи replay.",
        )

    checked_gateway = _CheckedGateway(gateway, profile, settings, prompts)

    service = build_analysis(
        checked_gateway,
        profile.model_copy(deep=True),
        settings,
        mode=settings.app_model_mode,
        assign_prompt_path=DEFAULT_ASSIGN_PROMPT_PATH,
        propose_prompt_path=_proposal_prompt_path(),
    )

    return PipelineAdapters(
        normalizer=normalizer,
        analysis=_CheckedAnalysis(
            service,
            profile.model_copy(deep=True),
        ),
        profile=profile,
    )
