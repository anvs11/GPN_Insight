from uuid import uuid4

import pytest

from app.config import REPO_ROOT, Settings
from app.demo import load_fixture


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
