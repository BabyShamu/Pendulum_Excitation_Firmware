from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ReadinessState(str, Enum):
    NOT_CONNECTED = "NOT CONNECTED"
    CHECK_REQUIRED = "CHECK REQUIRED"
    BIT_PASSED_HOME_REQUIRED = "BIT PASSED - HOME REQUIRED"
    SYSTEM_READY = "SYSTEM READY"
    FAULT = "FAULT"


class ManualMotionState(str, Enum):
    IDLE = "IDLE"
    STARTING_UP = "STARTING UP"
    MOVING_UP = "MOVING UP"
    STARTING_DOWN = "STARTING DOWN"
    MOVING_DOWN = "MOVING DOWN"
    STOPPING = "STOPPING"
    FAULT = "FAULT"


class BitResultState(str, Enum):
    NOT_RUN = "NOT RUN"
    CHECKING = "CHECKING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"


class CheckState(str, Enum):
    NOT_RUN = "NOT RUN"
    CHECKING = "CHECKING"
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"


class CheckName(str, Enum):
    COMMUNICATION = "STM32 COMMUNICATION"
    UPPER_LIMIT = "UPPER LIMIT INPUT"
    LOWER_LIMIT = "LOWER LIMIT INPUT"
    ANGLE_SENSOR = "ANGULAR SENSOR / AS5600"
    FIRMWARE_STATUS = "FIRMWARE STATUS"
    FAULT_STATE = "FAULT STATE"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: CheckName
    state: CheckState = CheckState.NOT_RUN
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MaintenanceSnapshot:
    readiness: ReadinessState = ReadinessState.NOT_CONNECTED
    bit_state: BitResultState = BitResultState.NOT_RUN
    checks: tuple[CheckResult, ...] = ()
    latest_check_at: datetime | None = None
    operation: str | None = None
    operation_message: str = ""
    current_angle_deg: float | None = None
    travel_span_steps: int | None = None
    position_steps: int | None = None
    steps_per_mm: float | None = None
    position_mm: float | None = None
    manual_motion: ManualMotionState = ManualMotionState.IDLE
    manual_motion_message: str = ""