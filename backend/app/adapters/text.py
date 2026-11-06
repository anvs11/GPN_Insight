"""Сборка текстового аналитического компонента из согласованных зависимостей."""

from ..config import Settings
from ..gateways import TextGateway
from ..schemas import ModelMode, ProcessingProfile
from ..analysis.service import AnalysisService


def build_analysis(
    gateway: TextGateway,
    profile: ProcessingProfile,
    settings: Settings,
    *,
    mode: ModelMode = "replay",
) -> AnalysisService:
    """Создаёт реализацию Analysis для Pipeline."""
    return AnalysisService(
        gateway=gateway,
        profile=profile,
        settings=settings,
        mode=mode,
    )