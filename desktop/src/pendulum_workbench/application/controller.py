from PySide6.QtCore import QObject

from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    HomeStatus,
)
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource
from pendulum_workbench.infrastructure.serial_client import (
    SerialPortInfo,
    SerialTelemetryClient,
)


class WorkbenchController(QObject):
    def __init__(
        self,
        state: ApplicationStateModel | None = None,
        mock_source: MockTelemetrySource | None = None,
        serial_client: SerialTelemetryClient | None = None,
    ) -> None:
        super().__init__()
        self.state = state or ApplicationStateModel()
        self.mock_source = mock_source or MockTelemetrySource()
        self.serial_client = serial_client or SerialTelemetryClient()
        self._disconnect_requested = False
        self.mock_source.sample_ready.connect(self.state.publish_sample)
        self.serial_client.connection_opened.connect(self._on_serial_opened)
        self.serial_client.connection_failed.connect(self._on_serial_failed)
        self.serial_client.connection_closed.connect(self._on_serial_closed)
        self.serial_client.telemetry_received.connect(self.state.publish_sample)
        self.serial_client.hardware_status_received.connect(self.state.set_hardware_status)
        self.serial_client.device_message_received.connect(self._on_device_message)
        self.serial_client.malformed_line_received.connect(self._on_malformed_line)
        self.serial_client.telemetry_stale_changed.connect(self._on_telemetry_stale)

    @staticmethod
    def available_ports() -> list[SerialPortInfo]:
        return SerialTelemetryClient.available_ports()

    def select_data_source(self, mode: DataSourceMode) -> None:
        if self.state.snapshot.connection in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
            ConnectionState.DISCONNECTING,
        ):
            raise RuntimeError("Disconnect the STM32 before changing data source")
        if mode == self.state.snapshot.data_source:
            return
        if mode == DataSourceMode.REAL:
            self.stop_mock()
            self.state.set_data_source(mode)
            self.state.report_event(
                EventSeverity.WARNING,
                "REAL STM32 mode selected; no device connected.",
            )
        else:
            self.state.set_data_source(mode)
            self.start_mock()

    def start_mock(self) -> None:
        if self.state.snapshot.data_source != DataSourceMode.MOCK:
            return
        if self.mock_source.is_active:
            return
        self.state.set_mock_source_active(True)
        self.state.report_event(
            EventSeverity.INFO,
            "Mock telemetry started; no STM32 connection is active.",
        )
        self.mock_source.start()

    def stop_mock(self) -> None:
        if not self.mock_source.is_active:
            return
        self.mock_source.stop()
        self.state.set_mock_source_active(False)
        self.state.report_event(EventSeverity.WARNING, "Mock telemetry paused.")

    def connect_real(self, port: str) -> None:
        if self.state.snapshot.data_source != DataSourceMode.REAL:
            raise RuntimeError("Select REAL STM32 mode before connecting")
        if self.state.snapshot.connection not in (
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        ):
            raise RuntimeError("A serial connection is already active")
        self._disconnect_requested = False
        self.state.begin_serial_session(port)
        self.state.report_event(EventSeverity.INFO, f"Connecting to {port} at 115200 baud.")
        try:
            self.serial_client.connect_to_port(port)
        except (RuntimeError, ValueError) as error:
            self.state.set_connection(ConnectionState.ERROR, port)
            self.state.report_event(EventSeverity.ERROR, str(error))

    def disconnect_real(self) -> None:
        if self.state.snapshot.connection not in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
        ):
            return
        self._disconnect_requested = True
        self.state.set_connection(ConnectionState.DISCONNECTING)
        self.state.report_event(EventSeverity.INFO, "Disconnecting serial transport.")
        self.serial_client.disconnect()

    def shutdown(self) -> None:
        self.stop_mock()
        self.serial_client.shutdown()

    def _on_serial_opened(self, capture_path: str) -> None:
        self.state.set_raw_capture_path(capture_path)
        if self.state.snapshot.connection != ConnectionState.DISCONNECTING:
            self.state.set_connection(
                ConnectionState.CONNECTED,
                self.state.snapshot.real_port,
            )
            self.state.report_event(
                EventSeverity.INFO,
                "Serial connected; read-only telemetry/status queries active.",
            )

    def _on_serial_failed(self, message: str) -> None:
        self.state.set_connection(ConnectionState.ERROR, self.state.snapshot.real_port)
        self.state.report_event(EventSeverity.ERROR, message)

    def _on_serial_closed(self, reason: str) -> None:
        if self._disconnect_requested:
            self.state.set_connection(ConnectionState.DISCONNECTED)
            self.state.report_event(EventSeverity.INFO, "Serial disconnected.")
        elif self.state.snapshot.connection != ConnectionState.ERROR:
            self.state.set_connection(ConnectionState.DISCONNECTED)
            self.state.report_event(EventSeverity.WARNING, reason)

    def _on_device_message(self, message: str) -> None:
        normalized = message.strip().lower()
        if normalized.startswith("homing failed:"):
            self.state.set_home_status(HomeStatus.HOMING_FAILED)
        elif normalized.startswith("homing complete:"):
            self.state.set_home_status(HomeStatus.HOMED)
        elif normalized.startswith("homing:"):
            self.state.set_home_status(HomeStatus.HOMING)
        if message and message != "Telemetry CSV header":
            self.state.report_event(EventSeverity.INFO, f"Device: {message[:180]}")

    def _on_malformed_line(self, message: str) -> None:
        self.state.report_event(
            EventSeverity.WARNING,
            f"Malformed serial input: {message[:180]}",
        )

    def _on_telemetry_stale(self, stale: bool) -> None:
        was_stale = self.state.snapshot.telemetry_stale
        self.state.set_telemetry_stale(stale)
        if stale:
            self.state.report_event(
                EventSeverity.WARNING,
                "No valid telemetry received for 1.5 seconds.",
            )
        elif was_stale and self.state.snapshot.connection == ConnectionState.CONNECTED:
            self.state.report_event(EventSeverity.INFO, "Valid telemetry stream restored.")
