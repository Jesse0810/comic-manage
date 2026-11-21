"""Background Qt jobs. Workers never access widgets."""

from threading import Event
from PySide6.QtCore import QObject, Signal, Slot

from .sources import Cancelled


class Worker(QObject):
    message = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, function, cancel: Event):
        super().__init__()
        self.function = function
        self.cancel = cancel

    @Slot()
    def run(self):
        try:
            self.succeeded.emit(self.function(self.message.emit, self.cancel))
        except Cancelled as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()
