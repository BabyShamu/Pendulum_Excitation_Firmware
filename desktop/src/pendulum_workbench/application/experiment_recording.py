import csv
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.experiments.repository import (
    TELEMETRY_COLUMNS,
    ExperimentRepository,
    ExperimentSession,
    LoadedExperiment,
)
from pendulum_workbench.infrastructure.raw_capture import RawSerialCapture
from pendulum_workbench.infrastructure.serial_client import (
    ReceivedSerialLine,
    SerialTelemetryClient,
)


class ExperimentRecordingService(QObject):
    recording_started = Signal(str, str)
    recording_finished = Signal(str, str)
    error_reported = Signal(str)

    def __init__(
        self,
        state: ApplicationStateModel,
        serial_client: SerialTelemetryClient,
        repository: ExperimentRepository | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.serial_client = serial_client
        self.repository = repository or ExperimentRepository()
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._active_session: ExperimentSession | None = None
        self._active_source: DataSourceMode | None = None
        self._writer_failed = False
        self._thread_started = False
        self._shutdown = False
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="pendulum-experiment-writer",
            daemon=True,
        )

        self.state.telemetry_received.connect(self._on_telemetry)
        self.serial_client.raw_line_received.connect(self._on_raw_line)
        self.serial_client.connection_closed.connect(self._on_transport_ended)
        self.serial_client.connection_failed.connect(self._on_transport_ended)
        self.error_reported.connect(self._on_error_reported)

    @property
    def is_recording(self) -> bool:
        return self._active_session is not None

    @property
    def active_session(self) -> ExperimentSession | None:
        return self._active_session

    def start_recording(self, name: str = "", notes: str = "") -> ExperimentSession:
        if self._shutdown:
            raise RuntimeError("The recording service has shut down")
        if self.is_recording:
            raise RuntimeError("An experiment recording is already active")

        snapshot = self.state.snapshot
        if snapshot.data_source == DataSourceMode.REAL:
            if snapshot.connection != ConnectionState.CONNECTED:
                raise RuntimeError("Connect to the STM32 before recording")
        elif snapshot.data_source == DataSourceMode.MOCK:
            if not snapshot.mock_source_active:
                raise RuntimeError("Start mock telemetry before recording")
        else:
            raise RuntimeError("Select a live telemetry source before recording")

        session = self.repository.create(
            name=name,
            notes=notes,
            data_source=snapshot.data_source,
            serial_port=snapshot.real_port,
        )
        if not self._thread_started:
            self._thread.start()
            self._thread_started = True
        self._writer_failed = False
        self.state.set_recording(RecordingState.STARTING)
        started = threading.Event()
        errors: list[str] = []
        self._queue.put(("start", session, started, errors))
        if not started.wait(5.0):
            self.state.set_recording(RecordingState.ERROR)
            raise TimeoutError("Timed out while opening experiment recording files")
        if errors:
            self.repository.finalize(
                session,
                completion_status="INTERRUPTED",
                sample_count=0,
                raw_line_count=0,
                interruption_reason=f"recording initialization failed: {errors[0]}",
            )
            self.state.set_recording(RecordingState.ERROR)
            raise OSError(errors[0])

        self._active_session = session
        self._active_source = snapshot.data_source
        self.state.set_recording(RecordingState.RECORDING)
        self.state.report_event(
            EventSeverity.INFO,
            f"Recording started: {session.manifest['name']} ({session.experiment_id}).",
        )
        self.recording_started.emit(session.experiment_id, str(session.directory))
        return session

    def stop_recording(
        self,
        *,
        interrupted: bool = False,
        reason: str | None = None,
    ) -> ExperimentSession | None:
        session = self._active_session
        if session is None:
            return None

        self.state.set_recording(RecordingState.STOPPING)
        completion_status = "INTERRUPTED" if interrupted or self._writer_failed else "COMPLETED"
        interruption_reason = reason
        if self._writer_failed and not interruption_reason:
            interruption_reason = "recording writer failed"
        stopped = threading.Event()
        errors: list[str] = []
        self._queue.put(
            ("stop", session, completion_status, interruption_reason, stopped, errors)
        )
        if not stopped.wait(10.0):
            self.state.set_recording(RecordingState.ERROR)
            self.state.report_event(
                EventSeverity.ERROR,
                f"Timed out while finalizing experiment {session.experiment_id}.",
            )
            raise TimeoutError("Timed out while finalizing experiment recording")
        self._active_session = None
        self._active_source = None
        if errors:
            completion_status = "INTERRUPTED"
            self.state.set_recording(RecordingState.ERROR)
            self.state.report_event(EventSeverity.ERROR, errors[0])
        else:
            self.state.set_recording(
                RecordingState.INTERRUPTED
                if completion_status == "INTERRUPTED"
                else RecordingState.SAVED
            )
            self.state.report_event(
                EventSeverity.WARNING if completion_status == "INTERRUPTED" else EventSeverity.INFO,
                f"Recording {completion_status.lower()}: {session.experiment_id}.",
            )
        self.recording_finished.emit(session.experiment_id, completion_status)
        return session

    def load_experiment(self, source: Path | str) -> LoadedExperiment:
        return self.repository.load(source)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        if self._active_session is not None:
            self.stop_recording(
                interrupted=True,
                reason="application closed before the recording was stopped",
            )
        if self._thread_started and self._thread.is_alive():
            self._queue.put(("shutdown",))
            self._thread.join(timeout=10.0)
        self._shutdown = True

    def _on_telemetry(self, sample: TelemetrySample) -> None:
        if self._active_session is not None:
            self._queue.put(("sample", sample))

    def _on_raw_line(self, line: ReceivedSerialLine) -> None:
        if self._active_session is not None and self._active_source == DataSourceMode.REAL:
            self._queue.put(("raw", line))

    def _on_transport_ended(self, reason: str) -> None:
        if self._active_session is not None and self._active_source == DataSourceMode.REAL:
            self.stop_recording(interrupted=True, reason=reason)

    def _on_error_reported(self, message: str) -> None:
        self._writer_failed = True
        self.state.set_recording(RecordingState.ERROR)
        self.state.report_event(EventSeverity.ERROR, f"Experiment recording failed: {message}")

    def _writer_loop(self) -> None:
        session: ExperimentSession | None = None
        raw_capture: RawSerialCapture | None = None
        telemetry_file = None
        telemetry_writer = None
        sample_count = 0
        raw_line_count = 0

        while True:
            item = self._queue.get()
            command = item[0]
            if command == "shutdown":
                if raw_capture is not None:
                    raw_capture.close()
                if telemetry_file is not None:
                    telemetry_file.close()
                return

            try:
                if command == "start":
                    session, started, errors = item[1:]
                    try:
                        raw_capture = RawSerialCapture(
                            session.directory,
                            session.experiment_id,
                            direct_directory=True,
                            append=True,
                        )
                        telemetry_file = (session.directory / "telemetry.csv").open(
                            "a", encoding="utf-8", newline=""
                        )
                        telemetry_writer = csv.writer(telemetry_file, lineterminator="\n")
                        telemetry_file.flush()
                        sample_count = 0
                        raw_line_count = 0
                    except Exception as error:
                        errors.append(str(error))
                        if raw_capture is not None:
                            raw_capture.close()
                            raw_capture = None
                        if telemetry_file is not None:
                            telemetry_file.close()
                            telemetry_file = None
                    finally:
                        started.set()
                elif command == "raw":
                    line: ReceivedSerialLine = item[1]
                    if raw_capture is not None:
                        raw_capture.write_line(
                            line.payload,
                            line.terminator,
                            line.received_elapsed_s,
                            line.received_at,
                            partial=line.partial,
                            source_rx_line_index=line.rx_line_index,
                        )
                        raw_line_count += 1
                elif command == "sample":
                    sample: TelemetrySample = item[1]
                    if telemetry_writer is not None and telemetry_file is not None:
                        quality = (
                            "valid"
                            if sample.angle_deg is not None and sample.position_mm is not None
                            else "missing_measurement"
                        )
                        telemetry_writer.writerow(
                            (
                                sample.sample_index,
                                sample.rx_line_index if sample.rx_line_index is not None else "",
                                self._csv_value(sample.device_time_s),
                                f"{sample.elapsed_s:.9f}",
                                self._csv_value(sample.position_mm),
                                self._csv_value(sample.angle_deg),
                                quality,
                                (sample.received_at or datetime.now(timezone.utc))
                                .astimezone(timezone.utc)
                                .isoformat(),
                            )
                        )
                        telemetry_file.flush()
                        sample_count += 1
                elif command == "stop":
                    (
                        session,
                        completion_status,
                        interruption_reason,
                        stopped,
                        errors,
                    ) = item[1:]
                    try:
                        if raw_capture is not None:
                            raw_capture.close()
                            raw_capture = None
                        if telemetry_file is not None:
                            telemetry_file.flush()
                            telemetry_file.close()
                            telemetry_file = None
                            telemetry_writer = None
                        self.repository.finalize(
                            session,
                            completion_status=completion_status,
                            sample_count=sample_count,
                            raw_line_count=raw_line_count,
                            interruption_reason=interruption_reason,
                        )
                    except Exception as error:
                        errors.append(str(error))
                    finally:
                        stopped.set()
            except Exception as error:
                self.error_reported.emit(str(error))

    @staticmethod
    def _csv_value(value: float | None) -> str:
        return "" if value is None else f"{value:.9g}"