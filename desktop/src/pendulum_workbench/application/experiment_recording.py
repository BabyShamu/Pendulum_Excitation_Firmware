import csv
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, Signal

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


@dataclass(frozen=True, slots=True)
class RecordingRequest:
    experiment_id: str
    name: str
    notes: str
    data_source: DataSourceMode
    serial_port: str | None


class ExperimentRecordingService(QObject):
    recording_requested = Signal(str, str)
    recording_started = Signal(str, str)
    recording_finished = Signal(str, str)
    recording_failed = Signal(str, str)
    error_reported = Signal(str)
    _writer_created = Signal(object)
    _writer_ready = Signal(object, str)
    _writer_failure = Signal(str, str)
    _writer_finalized = Signal(object, str, str)

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
        self._pending_request: RecordingRequest | None = None
        self._active_session: ExperimentSession | None = None
        self._active_source: DataSourceMode | None = None
        self._writer_failed = False
        self._stopping = False
        self._thread_started = False
        self._shutdown = False
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="pendulum-experiment-writer",
            daemon=True,
        )
        self._writer_created.connect(self._on_writer_created, Qt.ConnectionType.QueuedConnection)
        self._writer_ready.connect(self._on_writer_ready, Qt.ConnectionType.QueuedConnection)
        self._writer_failure.connect(self._on_writer_failed, Qt.ConnectionType.QueuedConnection)
        self._writer_finalized.connect(self._on_writer_finalized, Qt.ConnectionType.QueuedConnection)

        self.state.telemetry_received.connect(self._on_telemetry)
        self.serial_client.raw_line_received.connect(self._on_raw_line)
        self.serial_client.connection_closed.connect(self._on_transport_ended)
        self.serial_client.connection_failed.connect(self._on_transport_ended)
        self.error_reported.connect(self._on_error_reported)

    @property
    def is_recording(self) -> bool:
        return self._pending_request is not None or self._active_session is not None

    @property
    def is_starting(self) -> bool:
        return self._pending_request is not None

    @property
    def active_session(self) -> ExperimentSession | None:
        return self._active_session

    def start_recording(self, name: str = "", notes: str = "") -> str:
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

        request = RecordingRequest(
            experiment_id=str(uuid4()),
            name=name,
            notes=notes,
            data_source=snapshot.data_source,
            serial_port=snapshot.real_port,
        )
        self._pending_request = request
        self._active_source = snapshot.data_source
        if not self._thread_started:
            self._thread.start()
            self._thread_started = True
        self._writer_failed = False
        self._stopping = False
        self.state.set_recording(RecordingState.STARTING)
        self.state.report_event(
            EventSeverity.INFO,
            f"Start Recording clicked: {request.experiment_id}; creating folder "
            f"{self.repository.root / request.experiment_id}.",
        )
        self._queue.put(("start", request))
        self.recording_requested.emit(
            request.experiment_id,
            str(self.repository.root / request.experiment_id),
        )
        return request.experiment_id

    def _on_writer_created(self, session: ExperimentSession) -> None:
        if self._pending_request is None or self._pending_request.experiment_id != session.experiment_id:
            return
        self.state.report_event(
            EventSeverity.INFO,
            f"Experiment session/folder created: {session.experiment_id} at {session.directory}.",
        )

    def stop_recording(
        self,
        *,
        interrupted: bool = False,
        reason: str | None = None,
    ) -> ExperimentSession | None:
        session = self._active_session
        request = self._pending_request
        if session is None and request is None:
            return None
        if self._stopping:
            return session

        self.state.set_recording(RecordingState.STOPPING)
        completion_status = "INTERRUPTED" if interrupted or self._writer_failed else "COMPLETED"
        interruption_reason = reason
        if self._writer_failed and not interruption_reason:
            interruption_reason = "recording writer failed"
        experiment_id = session.experiment_id if session is not None else request.experiment_id
        self._stopping = True
        self.state.report_event(EventSeverity.INFO, f"Stop Recording requested: {experiment_id}.")
        self._queue.put(("stop", experiment_id, completion_status, interruption_reason))
        return session

    def load_experiment(self, source: Path | str) -> LoadedExperiment:
        return self.repository.load(source)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        if self.is_recording:
            self.stop_recording(
                interrupted=True,
                reason="application closed before the recording was stopped",
            )
        self._shutdown = True
        if self._thread_started and self._thread.is_alive():
            finished = threading.Event()
            self._queue.put(("shutdown", finished))
            finished.wait(10.0)
            self._thread.join(timeout=10.0)

    def _on_telemetry(self, sample: TelemetrySample) -> None:
        if self.is_recording and not self._stopping:
            self._queue.put(("sample", sample))

    def _on_raw_line(self, line: ReceivedSerialLine) -> None:
        if self.is_recording and not self._stopping and self._active_source == DataSourceMode.REAL:
            self._queue.put(("raw", line))

    def _on_transport_ended(self, reason: str) -> None:
        if self.is_recording and self._active_source == DataSourceMode.REAL:
            self.stop_recording(interrupted=True, reason=reason)

    def _on_error_reported(self, message: str) -> None:
        self._writer_failed = True
        self.state.set_recording(RecordingState.ERROR)
        self.state.report_event(EventSeverity.ERROR, f"Experiment recording failed: {message}")

    def _on_writer_ready(self, session: ExperimentSession, raw_path: str) -> None:
        request = self._pending_request
        if request is None or request.experiment_id != session.experiment_id:
            return
        self._pending_request = None
        self._active_session = session
        self.state.set_raw_capture_path(raw_path)
        if not self._stopping:
            self.state.set_recording(RecordingState.RECORDING)
            self.state.report_event(
                EventSeverity.INFO,
                f"RECORDING state entered for {session.experiment_id}.",
            )
        self.state.report_event(
            EventSeverity.INFO,
            f"Recording writer ready for {session.experiment_id} at {session.directory}.",
        )
        if not self._stopping:
            self.recording_started.emit(session.experiment_id, str(session.directory))

    def _on_writer_failed(self, experiment_id: str, message: str) -> None:
        if self._pending_request is not None and self._pending_request.experiment_id == experiment_id:
            self._pending_request = None
            self._active_source = None
            self._stopping = False
            self.state.set_recording(RecordingState.ERROR)
            self.state.report_event(EventSeverity.ERROR, message)
            self.recording_failed.emit(experiment_id, message)
            return
        if self._active_session is not None and self._active_session.experiment_id == experiment_id:
            self._writer_failed = True
            self.state.report_event(EventSeverity.ERROR, message)
            self.stop_recording(interrupted=True, reason=message)

    def _on_writer_finalized(
        self,
        session: ExperimentSession,
        completion_status: str,
        error: str,
    ) -> None:
        if self._active_session is None or self._active_session.experiment_id != session.experiment_id:
            return
        self._active_session = None
        self._pending_request = None
        self._active_source = None
        self._stopping = False
        if error:
            self.state.set_recording(RecordingState.ERROR)
            self.state.report_event(EventSeverity.ERROR, error)
            self.recording_failed.emit(session.experiment_id, error)
            return
        self.state.set_recording(
            RecordingState.INTERRUPTED
            if completion_status == "INTERRUPTED"
            else RecordingState.SAVED
        )
        severity = EventSeverity.WARNING if completion_status == "INTERRUPTED" else EventSeverity.INFO
        self.state.report_event(
            severity,
            f"Recording {completion_status.lower()}: {session.experiment_id}.",
        )
        self.recording_finished.emit(session.experiment_id, completion_status)

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
                item[1].set()
                return

            try:
                if command == "start":
                    request: RecordingRequest = item[1]
                    session = None
                    try:
                        session = self.repository.create(
                            name=request.name,
                            notes=request.notes,
                            data_source=request.data_source,
                            serial_port=request.serial_port,
                            experiment_id=request.experiment_id,
                        )
                        self._writer_created.emit(session)
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
                        self._writer_ready.emit(
                            session,
                            str(session.directory / "serial_capture.log"),
                        )
                    except Exception as error:
                        if raw_capture is not None:
                            raw_capture.close()
                            raw_capture = None
                        if telemetry_file is not None:
                            telemetry_file.close()
                            telemetry_file = None
                        reason = (
                            f"Could not initialize experiment {request.experiment_id} "
                            f"at {self.repository.root / request.experiment_id}: "
                            f"{type(error).__name__}: {error}"
                        )
                        if session is not None:
                            try:
                                self.repository.finalize(
                                    session,
                                    completion_status="INTERRUPTED",
                                    sample_count=0,
                                    raw_line_count=0,
                                    interruption_reason=reason,
                                )
                            except Exception as finalize_error:
                                reason += f"; manifest finalization failed: {finalize_error}"
                        self._writer_failure.emit(request.experiment_id, reason)
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
                    experiment_id, completion_status, interruption_reason = item[1:]
                    if session is None or session.experiment_id != experiment_id:
                        continue
                    error = ""
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
                    except Exception as finalize_error:
                        error = (
                            f"Could not finalize experiment {experiment_id} "
                            f"at {session.directory}: {type(finalize_error).__name__}: {finalize_error}"
                        )
                    self._writer_finalized.emit(session, completion_status, error)
                    session = None
            except Exception as error:
                if session is not None:
                    self._writer_failure.emit(
                        session.experiment_id,
                        f"Recording write failed at {session.directory}: "
                        f"{type(error).__name__}: {error}",
                    )
                else:
                    self.error_reported.emit(str(error))

    @staticmethod
    def _csv_value(value: float | None) -> str:
        return "" if value is None else f"{value:.9g}"