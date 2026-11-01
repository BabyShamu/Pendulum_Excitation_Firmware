import faulthandler
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer


class RuntimeDiagnostics(QObject):
    _HEARTBEAT_INTERVAL_MS = 100

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        configured_path = os.environ.get("PENDULUM_WORKBENCH_DIAGNOSTICS")
        self._file = None
        self._records: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._writer: threading.Thread | None = None
        self._last_heartbeat = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(self._HEARTBEAT_INTERVAL_MS)
        self._timer.timeout.connect(self._heartbeat)
        if configured_path:
            path = Path(configured_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8", buffering=1)
            faulthandler.enable(self._file)
            faulthandler.dump_traceback_later(5.0, repeat=True, file=self._file)
            self._writer = threading.Thread(
                target=self._write_records,
                name="pendulum-runtime-diagnostics",
                daemon=True,
            )
            self._writer.start()
            self._timer.start()
            self.record("diagnostics_started", path=str(path))

    @property
    def enabled(self) -> bool:
        return self._file is not None

    def record(self, event: str, **fields: Any) -> None:
        if self._file is None:
            return
        payload = {
            "monotonic_s": time.monotonic(),
            "thread": threading.current_thread().name,
            "event": event,
            **fields,
        }
        self._records.put_nowait(payload)

    def _write_records(self) -> None:
        while True:
            payload = self._records.get()
            if payload is None:
                return
            file = self._file
            if file is not None:
                file.write(json.dumps(payload, default=str) + "\n")

    def _heartbeat(self) -> None:
        now = time.monotonic()
        latency_ms = max(
            0.0,
            (now - self._last_heartbeat) * 1000.0 - self._HEARTBEAT_INTERVAL_MS,
        )
        self._last_heartbeat = now
        self.record("qt_heartbeat", latency_ms=latency_ms)

    def close(self) -> None:
        if self._file is None:
            return
        self._timer.stop()
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        self.record("diagnostics_stopped")
        self._records.put(None)
        if self._writer is not None:
            self._writer.join(timeout=1.0)
        self._file.close()
        self._file = None
