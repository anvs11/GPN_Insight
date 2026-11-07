"""Сборка текстового аналитического компонента из согласованных зависимостей."""

from pathlib import Path

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
    assign_prompt_path: Path | None = None,
    propose_prompt_path: Path | None = None,
) -> AnalysisService:
    """Создаёт реализацию Analysis для Pipeline."""
    return AnalysisService(
        gateway=gateway,
        profile=profile,
        settings=settings,
        mode=mode,
        assign_prompt_path=assign_prompt_path,
        propose_prompt_path=propose_prompt_path,
    )
