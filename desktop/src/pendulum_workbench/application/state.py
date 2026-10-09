from dataclasses import replace
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal

from pendulum_workbench.domain.models import (
    AppEvent,
    ApplicationSnapshot,
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    HardwareStatus,
    HomeStatus,
    MotionState,
    RecordingState,
    TelemetrySample,
)


class ApplicationStateModel(QObject):
    snapshot_changed = Signal(object)
    telemetry_received = Signal(object)
    event_added = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._snapshot = ApplicationSnapshot()

    @property
    def snapshot(self) -> ApplicationSnapshot:
        return self._snapshot

    def set_mock_source_active(self, active: bool) -> None:
        self._update(mock_source_active=active)

    def set_data_source(self, mode: DataSourceMode) -> None:
        self._update(
            connection=ConnectionState.DISCONNECTED,
            real_port=None,
            data_source=mode,
            mock_source_active=False,
            samples_received=0,
            latest_sample=None,
            hardware_status=HardwareStatus(),
            home_status=HomeStatus.UNKNOWN,
            telemetry_stale=False,
            last_valid_telemetry_at=None,
            raw_capture_path=None,
            motion=MotionState.UNKNOWN,
        )

    def set_connection(self, state: ConnectionState, port: str | None = None) -> None:
        changes: dict[str, object] = {"connection": state}
        if port is not None:
            changes["real_port"] = port
        elif state == ConnectionState.DISCONNECTED:
            changes["real_port"] = None
        self._update(**changes)

    def begin_serial_session(self, port: str) -> None:
        self._update(
            connection=ConnectionState.CONNECTING,
            real_port=port,
            samples_received=0,
            latest_sample=None,
            hardware_status=HardwareStatus(),
            home_status=HomeStatus.UNKNOWN,
            telemetry_stale=False,
            last_valid_telemetry_at=None,
            raw_capture_path=None,
            motion=MotionState.UNKNOWN,
        )

    def set_raw_capture_path(self, path: str) -> None:
        self._update(raw_capture_path=path)

    def set_hardware_status(self, status: HardwareStatus) -> None:
        current = self._snapshot.hardware_status
        merged = HardwareStatus(
            homed=status.homed if status.homed is not None else current.homed,
            upper_limit=(
                status.upper_limit if status.upper_limit is not None else current.upper_limit
            ),
            lower_limit=(
                status.lower_limit if status.lower_limit is not None else current.lower_limit
            ),
            active_motion_mode=(
                status.active_motion_mode
                if status.active_motion_mode != "Unknown"
                else current.active_motion_mode
            ),
            fault=status.fault if status.fault_known else current.fault,
            fault_known=status.fault_known or current.fault_known,
            updated_at=status.updated_at or current.updated_at,
        )
        motion = self._snapshot.motion
        home_status = self._snapshot.home_status
        if status.homed is True:
            home_status = HomeStatus.HOMED
        elif status.homed is False and home_status not in (
            HomeStatus.HOMING,
            HomeStatus.HOMING_FAILED,
        ):
            home_status = HomeStatus.HOME_REQUIRED
        if merged.fault:
            motion = MotionState.FAULT
        elif merged.active_motion_mode in (
            "Sine", "Parametric", "Parametric closed loop", "Manual stepping"
        ):
            motion = MotionState.RUNNING
        elif merged.active_motion_mode in ("Idle", "Parametric test (no motion)"):
            motion = MotionState.READY if merged.homed else MotionState.IDLE
        self._update(hardware_status=merged, motion=motion, home_status=home_status)

    def set_home_status(self, status: HomeStatus) -> None:
        self._update(home_status=status)

    def set_saved_telemetry(self, samples: tuple[TelemetrySample, ...]) -> None:
        latest = samples[-1] if samples else None
        self._update(
            samples_received=len(samples),
            latest_sample=latest,
            last_valid_telemetry_at=latest.received_at if latest is not None else None,
            telemetry_stale=False,
        )

    def set_telemetry_stale(self, stale: bool) -> None:
        self._update(telemetry_stale=stale)

    def set_motion(self, state: MotionState) -> None:
        self._update(motion=state)

    def set_recording(self, state: RecordingState) -> None:
        self._update(recording=state)

    def publish_sample(self, sample: TelemetrySample) -> None:
        received_at = sample.received_at or datetime.now(timezone.utc)
        self._update(
            samples_received=self._snapshot.samples_received + 1,
            latest_sample=sample,
            last_valid_telemetry_at=received_at,
            telemetry_stale=False,
        )
        self.telemetry_received.emit(sample)

    def report_event(self, severity: EventSeverity, message: str) -> None:
        self.event_added.emit(AppEvent.now(severity, message))

    def _update(self, **changes: object) -> None:
        self._snapshot = replace(self._snapshot, **changes)
        self.snapshot_changed.emit(self._snapshot)