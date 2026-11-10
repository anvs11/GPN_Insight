from uuid import uuid4
import os

import pytest

from app.config import REPO_ROOT, Settings
from app.demo import load_fixture


@pytest.fixture(autouse=True)
def isolate_weekly_clock(monkeypatch):
    # Обычные тесты не запускают фоновую запись по реальным часам.
    # Расписание проверяется отдельно с явным enabled=True и fixed clock.
    monkeypatch.setenv("APP_WEEKLY_ARCHIVE_ENABLED", "false")


@pytest.fixture
def tmp_path():
    # Изолируем запуски и избегаем чужих ACL системного Windows Temp.
    path = REPO_ROOT / "var" / "pytest" / uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture
def fixture():
    return load_fixture(REPO_ROOT / "data/contracts/demo_state.json")


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, app_db_path=tmp_path / "insight.db")


@pytest.fixture
def create_symlink():
    def create(link, target):
        try:
            link.symlink_to(target)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                pytest.skip("Windows WinError 1314: нет права создания symlink; реальный link-case не проверен")
            raise
    return create
