from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ConnectionState(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    DISCONNECTING = "Disconnecting"
    ERROR = "Error"


class MotionState(str, Enum):
    UNKNOWN = "Unknown"
    IDLE = "Idle"
    HOMING = "Homing"
    READY = "Ready"
    RUNNING = "Running"
    STOPPING = "Stopping"
    FAULT = "Fault"


class HomeStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    HOME_REQUIRED = "HOME REQUIRED"
    HOMING = "HOMING..."
    HOMED = "HOMED"
    HOMING_FAILED = "HOMING FAILED"


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


class DataSourceMode(str, Enum):
    MOCK = "Mock"
    REAL = "Real STM32"


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    sample_index: int
    elapsed_s: float
    angle_deg: float | None
    position_mm: float | None
    source: str = "mock"
    device_time_s: float | None = None
    received_at: datetime | None = None
    rx_line_index: int | None = None


@dataclass(frozen=True, slots=True)
class HardwareStatus:
    homed: bool | None = None
    upper_limit: bool | None = None
    lower_limit: bool | None = None
    active_motion_mode: str = "Unknown"
    fault: str | None = None
    fault_known: bool = False
    updated_at: datetime | None = None


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
    data_source: DataSourceMode = DataSourceMode.MOCK
    motion: MotionState = MotionState.UNKNOWN
    home_status: HomeStatus = HomeStatus.UNKNOWN
    recording: RecordingState = RecordingState.OFF
    mock_source_active: bool = False
    real_port: str | None = None
    samples_received: int = 0
    latest_sample: TelemetrySample | None = None
    hardware_status: HardwareStatus = HardwareStatus()
    telemetry_stale: bool = False
    last_valid_telemetry_at: datetime | None = None
    raw_capture_path: str | None = None