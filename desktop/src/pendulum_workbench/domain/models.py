from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ConnectionState(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    ERROR = "Error"


class MotionState(str, Enum):
    UNKNOWN = "Unknown"
    IDLE = "Idle"
    HOMING = "Homing"
    READY = "Ready"
    RUNNING = "Running"
    STOPPING = "Stopping"
    FAULT = "Fault"


class RecordingState(str, Enum):
    OFF = "Off"
    STARTING = "Starting"
    RECORDING = "Recording"
    STOPPING = "Stopping"
    SAVED = "Saved"
    INTERRUPTED = "Interrupted"
    ERROR = "Error"


class EventSeverity(str, Enum):
    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    sample_index: int
    elapsed_s: float
    angle_deg: float
    position_mm: float
    source: str = "mock"


@dataclass(frozen=True, slots=True)
class AppEvent:
    timestamp: datetime
    severity: EventSeverity
    message: str

    @classmethod
    def now(cls, severity: EventSeverity, message: str) -> "AppEvent":
        return cls(datetime.now(timezone.utc), severity, message)


@dataclass(frozen=True, slots=True)
class ApplicationSnapshot:
    connection: ConnectionState = ConnectionState.DISCONNECTED
    motion: MotionState = MotionState.UNKNOWN
    recording: RecordingState = RecordingState.OFF
    mock_source_active: bool = False
    samples_received: int = 0
    latest_sample: TelemetrySample | None = None