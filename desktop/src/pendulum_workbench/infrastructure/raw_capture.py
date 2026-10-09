import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO
from uuid import uuid4


class RawSerialCapture:
    def __init__(
        self,
        root: Path,
        session_id: str | None = None,
        *,
        direct_directory: bool = False,
        append: bool = False,
    ) -> None:
        self.session_id = session_id or str(uuid4())
        self.directory = root if direct_directory else root / self.session_id
        self.directory.mkdir(parents=True, exist_ok=direct_directory)
        self.path = self.directory / "serial_capture.log"
        if append and not self.path.exists():
            raise FileNotFoundError(self.path)
        self._file: TextIO = self.path.open(
            "a" if append else "x",
            encoding="utf-8",
            newline="\n",
        )
        self._rx_line_index = 0

    def write_line(
        self,
        payload: bytes,
        terminator: bytes,
        received_elapsed_s: float,
        received_at: datetime | None = None,
        *,
        partial: bool = False,
        source_rx_line_index: int | None = None,
    ) -> int:
        if self._file.closed:
            raise ValueError("serial capture is closed")
        if terminator not in (b"\r\n", b"\n", b"\r", b""):
            raise ValueError("unsupported line terminator")

        if source_rx_line_index is None:
            self._rx_line_index += 1
        else:
            self._rx_line_index = source_rx_line_index
        received_at = received_at or datetime.now(timezone.utc)
        record = {
            "rx_line_index": self._rx_line_index,
            "received_at_utc": received_at.astimezone(timezone.utc).isoformat(),
            "received_elapsed_s": received_elapsed_s,
            "terminator": {
                b"\r\n": "CRLF",
                b"\n": "LF",
                b"\r": "CR",
                b"": "NONE",
            }[terminator],
            "raw_bytes_base64": base64.b64encode(payload).decode("ascii"),
            "partial": partial,
        }
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()
        return self._rx_line_index

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            self._file.close()


def default_capture_root() -> Path:
    app_data = os.environ.get("LOCALAPPDATA")
    if app_data:
        return Path(app_data) / "PendulumWorkbench" / "serial-captures"
    return Path.home() / ".local" / "share" / "pendulum-workbench" / "serial-captures"