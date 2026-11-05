"""Короткая общая защита одного процесса; не файловая транзакция."""

from contextlib import contextmanager
from pathlib import Path
from threading import Condition, Lock, get_ident

from .errors import RepositoryError


class OperationGate:
    def __init__(self):
        self.condition = Condition()
        self.import_owner: int | None = None
        self.import_depth = 0
        self.starting = False

    @contextmanager
    def importing(self, *, wait=True):
        owner = get_ident()
        with self.condition:
            if self.starting:
                raise RepositoryError("RUN_ACTIVE", "Запуск начинается. Дождитесь его завершения перед фиксацией импорта.")
            while self.import_owner not in {None, owner}:
                if not wait:
                    raise RepositoryError("IMPORT_ACTIVE", "Другой импорт фиксируется. Повторите после его завершения.")
                self.condition.wait()
                if self.starting:
                    raise RepositoryError("RUN_ACTIVE", "Запуск начинается. Повторите импорт после его завершения.")
            self.import_owner = owner
            self.import_depth += 1
        try:
            yield
        finally:
            with self.condition:
                self.import_depth -= 1
                if not self.import_depth:
                    self.import_owner = None
                self.condition.notify_all()

    @contextmanager
    def start_run(self):
        with self.condition:
            if self.import_owner is not None:
                raise RepositoryError("IMPORT_ACTIVE", "Идёт фиксация импорта. Повторите запуск после её окончания.")
            while self.starting:
                self.condition.wait()
                if self.import_owner is not None:
                    raise RepositoryError("IMPORT_ACTIVE", "Идёт фиксация импорта. Повторите запуск позже.")
            self.starting = True
        try:
            yield
        finally:
            with self.condition:
                self.starting = False
                self.condition.notify_all()


_gates: dict[Path, OperationGate] = {}
_registry_lock = Lock()


def gate_for(path: Path) -> OperationGate:
    with _registry_lock:
        return _gates.setdefault(path.resolve(), OperationGate())
