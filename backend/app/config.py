from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .schemas import ModelMode

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_model_mode: ModelMode = "replay"
    app_db_path: Path = Path("var/insight.db")
    app_data_root: Path = Path("data")
    app_upload_root: Path = Path("var/uploads")
    app_tmp_root: Path = Path("var/tmp")
    app_recordings_root: Path = Path("data/recordings/text")
    app_replay_model_id: str = ""
    app_replay_normalization_hash: str = ""
    app_processing_profile_path: Path | None = None
    app_media_recordings_path: Path | None = None
    app_media_cache_root: Path = Path("var/media-success-cache")
    text_model_base_url: str = ""
    text_model_id: str = ""
    text_model_token: SecretStr = SecretStr("")
    asr_base_url: str = ""
    asr_model_id: str = ""
    asr_token: SecretStr = SecretStr("")
    vl_base_url: str = ""
    vl_model_id: str = ""
    vl_token: SecretStr = SecretStr("")
    app_issue_hosts: str = ""
    app_p_max: Annotated[int, Field(ge=1)] = 50
    app_retrieval_k: Annotated[int, Field(ge=1)] = 20
    app_passport_batch_max_chars: Annotated[int, Field(ge=1)] = 16000
    app_max_assign_batches: Annotated[int, Field(ge=1)] = 8
    app_max_image_pixels: Annotated[int, Field(ge=1)] = 25000000
    app_max_audio_seconds: Annotated[int, Field(ge=1)] = 60
    app_duplicate_suggestions: bool = False

    def repo_path(self, path: Path) -> Path:
        """Одинаковые пути при запуске из корня и backend/."""
        return (REPO_ROOT / path).resolve()

    @property
    def effective_retrieval_k(self) -> int:
        return min(self.app_retrieval_k, self.app_p_max)
