from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
)

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.maintenance import (
    BitResultState,
    CheckName,
    CheckState,
    MaintenanceSnapshot,
    ManualMotionState,
    PendulumLatchState,
    ReadinessState,
)
from pendulum_workbench.domain.models import (
    ApplicationSnapshot,
    ConnectionState,
    EventSeverity,
    HomeStatus,
    MotionState,
    RecordingState,
)


class MaintenancePage(QWidget):
    _READINESS_COLORS = {
        ReadinessState.NOT_CONNECTED: ("#66736f", "#eef1ef"),
        ReadinessState.CHECK_REQUIRED: ("#8b5b14", "#fff3d8"),
        ReadinessState.BIT_PASSED_HOME_REQUIRED: ("#8b5b14", "#fff3d8"),
        ReadinessState.SYSTEM_READY: ("#205f49", "#e5f4ed"),
        ReadinessState.FAULT: ("#a3322b", "#fbe8e6"),
    }
    _CHECK_COLORS = {
        CheckState.NOT_RUN: ("#71807d", "#f0f3f1"),
        CheckState.CHECKING: ("#8b5b14", "#fff3d8"),
        CheckState.PASS: ("#205f49", "#e5f4ed"),
        CheckState.FAIL: ("#a3322b", "#fbe8e6"),
        CheckState.WARNING: ("#8b5b14", "#fff3d8"),
    }

    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self.maintenance = controller.maintenance

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("maintenanceScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.scroll_area.setWidget(self.content)
        outer_layout.addWidget(self.scroll_area)

        readiness_panel = QFrame()
        readiness_panel.setObjectName("surface")
        self._apply_card_depth(readiness_panel)
        readiness_layout = QHBoxLayout(readiness_panel)
        readiness_layout.setContentsMargins(16, 12, 16, 12)
        readiness_layout.setSpacing(12)
        readiness_text = QVBoxLayout()
        readiness_heading = QLabel("SYSTEM READINESS")
        readiness_heading.setObjectName("sectionTitle")
        readiness_text.addWidget(readiness_heading)
        self.readiness_detail = QLabel("Connect to the STM32 to begin maintenance checks.")
        self.readiness_detail.setObjectName("subtle")
        self.readiness_detail.setWordWrap(True)
        readiness_text.addWidget(self.readiness_detail)
        readiness_layout.addLayout(readiness_text, 1)
        self.readiness_indicator = QLabel(ReadinessState.NOT_CONNECTED.value)
        self.readiness_indicator.setObjectName("readinessIndicator")
        self.readiness_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.readiness_indicator.setMinimumWidth(210)
        readiness_layout.addWidget(self.readiness_indicator)
        layout.addWidget(readiness_panel)

        bit_panel = QFrame()
        bit_panel.setObjectName("surface")
        self._apply_card_depth(bit_panel)
        bit_layout = QVBoxLayout(bit_panel)
        bit_layout.setContentsMargins(16, 12, 16, 12)
        bit_layout.setSpacing(8)
        bit_header = QHBoxLayout()
        bit_heading = QLabel("POWER-UP BIT / SYSTEM CHECK")
        bit_heading.setObjectName("sectionTitle")
        bit_header.addWidget(bit_heading)
        bit_header.addStretch(1)
        self.bit_result_value = QLabel(BitResultState.NOT_RUN.value)
        self.bit_result_value.setObjectName("bitResultValue")
        bit_header.addWidget(self.bit_result_value)
        bit_layout.addLayout(bit_header)

        self.check_rows: dict[CheckName, tuple[QLabel, QLabel]] = {}
        check_grid = QGridLayout()
        check_grid.setContentsMargins(0, 0, 0, 0)
        check_grid.setHorizontalSpacing(10)
        check_grid.setVerticalSpacing(4)
        for row, name in enumerate(CheckName):
            name_label = QLabel(name.value)
            name_label.setObjectName("maintenanceCheckName")
            detail_label = QLabel("Not checked")
            detail_label.setObjectName("maintenanceCheckDetail")
            detail_label.setWordWrap(True)
            state_label = QLabel(CheckState.NOT_RUN.value)
            state_label.setObjectName("maintenanceCheckState")
            state_label.setMinimumWidth(80)
            state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            check_grid.addWidget(name_label, row, 0)
            check_grid.addWidget(detail_label, row, 1)
            check_grid.addWidget(state_label, row, 2)
            self.check_rows[name] = (state_label, detail_label)
        check_grid.setColumnStretch(1, 1)
        bit_layout.addLayout(check_grid)

        bit_controls = QHBoxLayout()
        self.run_system_check_button = QPushButton("Run System Check")
        self.run_system_check_button.setObjectName("runSystemCheckButton")
        self.run_system_check_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.run_system_check_button.clicked.connect(self._run_system_check)
        bit_controls.addWidget(self.run_system_check_button)
        self.clear_fault_button = QPushButton("Clear Fault")
        self.clear_fault_button.setObjectName("clearFirmwareFaultButton")
        self.clear_fault_button.clicked.connect(self._clear_fault)
        bit_controls.addWidget(self.clear_fault_button)
        self.bit_timestamp_value = QLabel("No completed check")
        self.bit_timestamp_value.setObjectName("subtle")
        bit_controls.addWidget(self.bit_timestamp_value, 1)
        bit_layout.addLayout(bit_controls)
        self.clear_fault_result_value = QLabel("")
        self.clear_fault_result_value.setObjectName("subtle")
        self.clear_fault_result_value.setWordWrap(True)
        bit_layout.addWidget(self.clear_fault_result_value)
        layout.addWidget(bit_panel)

        home_panel = QFrame()
        home_panel.setObjectName("surface")
        self._apply_card_depth(home_panel)
        home_layout = QVBoxLayout(home_panel)
        home_layout.setContentsMargins(16, 12, 16, 12)
        home_layout.setSpacing(8)
        home_heading = QLabel("HOMING & CALIBRATION")
        home_heading.setObjectName("sectionTitle")
        home_layout.addWidget(home_heading)

        home_row = QHBoxLayout()
        self.home_state_value = QLabel(HomeStatus.UNKNOWN.value)
        self.home_state_value.setObjectName("homeStateValue")
        home_row.addWidget(self.home_state_value)
        home_row.addStretch(1)
        self.home_button = QPushButton("Home")
        self.home_button.setObjectName("maintenanceHomeButton")
        self.home_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.home_button.clicked.connect(self._start_homing)
        home_row.addWidget(self.home_button)
        home_layout.addLayout(home_row)

        self.home_latch_interlock_note = QLabel("Homing requires Pendulum Latch OPEN.")
        self.home_latch_interlock_note.setObjectName("subtle")
        self.home_latch_interlock_note.setWordWrap(True)
        home_layout.addWidget(self.home_latch_interlock_note)

        self.home_progress_value = QLabel("Homing has not been run in this session.")
        self.home_progress_value.setObjectName("subtle")
        self.home_progress_value.setWordWrap(True)
        home_layout.addWidget(self.home_progress_value)

        diagnostic_grid = QGridLayout()
        diagnostic_grid.setHorizontalSpacing(10)
        diagnostic_grid.setVerticalSpacing(4)
        self.upper_limit_value = self._diagnostic_value("Upper limit")
        self.lower_limit_value = self._diagnostic_value("Lower limit")
        self.travel_span_value = self._diagnostic_value("Measured travel")
        self.pivot_position_value = self._diagnostic_value("Pivot position")
        for row, (label, value) in enumerate(
            (
                ("UPPER LIMIT", self.upper_limit_value),
                ("LOWER LIMIT", self.lower_limit_value),
                ("TRAVEL SPAN", self.travel_span_value),
                ("PIVOT POSITION", self.pivot_position_value),
            )
        ):
            key = QLabel(label)
            key.setObjectName("hardwareInfoKey")
            diagnostic_grid.addWidget(key, row // 2, (row % 2) * 2)
            diagnostic_grid.addWidget(value, row // 2, (row % 2) * 2 + 1)
        diagnostic_grid.setColumnStretch(1, 1)
        diagnostic_grid.setColumnStretch(3, 1)
        home_layout.addLayout(diagnostic_grid)

        zero_header = QHBoxLayout()
        zero_heading = QLabel("ANGLE SENSOR ZERO")
        zero_heading.setObjectName("sectionTitle")
        zero_header.addWidget(zero_heading)
        zero_header.addStretch(1)
        self.current_angle_value = QLabel("Current angle: unavailable")
        self.current_angle_value.setObjectName("hardwareInfoValue")
        zero_header.addWidget(self.current_angle_value)
        self.zero_angle_button = QPushButton("Set Current Angle as Zero")
        self.zero_angle_button.setObjectName("zeroAngleButton")
        self.zero_angle_button.clicked.connect(self._confirm_angle_zero)
        zero_header.addWidget(self.zero_angle_button)
        home_layout.addLayout(zero_header)
        self.zero_result_value = QLabel("Angle reference unchanged")
        self.zero_result_value.setObjectName("subtle")
        self.zero_result_value.setWordWrap(True)
        home_layout.addWidget(self.zero_result_value)
        layout.addWidget(home_panel)

        manual_panel = QFrame()
        manual_panel.setObjectName("surface")
        self._apply_card_depth(manual_panel)
        manual_layout = QVBoxLayout(manual_panel)
        manual_layout.setContentsMargins(16, 12, 16, 12)
        manual_layout.setSpacing(8)
        manual_heading = QLabel("MANUAL PIVOT CONTROL")
        manual_heading.setObjectName("sectionTitle")
        manual_layout.addWidget(manual_heading)
        self.manual_motion_state_value = QLabel(ManualMotionState.IDLE.value)
        self.manual_motion_state_value.setObjectName("manualMotionState")
        self.manual_motion_state_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        manual_layout.addWidget(self.manual_motion_state_value)

        manual_buttons = QHBoxLayout()
        manual_buttons.setSpacing(12)
        self.manual_up_button = QPushButton("MOVE UP")
        self.manual_up_button.setObjectName("manualMoveButton")
        self.manual_up_button.setProperty("direction", "up")
        self.manual_up_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
        )
        self.manual_up_button.setMinimumHeight(72)
        self.manual_up_button.pressed.connect(lambda: self._start_manual_motion("up"))
        self.manual_up_button.released.connect(lambda: self._stop_manual_motion("button_release"))
        manual_buttons.addWidget(self.manual_up_button, 1)
        self.manual_down_button = QPushButton("MOVE DOWN")
        self.manual_down_button.setObjectName("manualMoveButton")
        self.manual_down_button.setProperty("direction", "down")
        self.manual_down_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.manual_down_button.setMinimumHeight(72)
        self.manual_down_button.pressed.connect(lambda: self._start_manual_motion("down"))
        self.manual_down_button.released.connect(lambda: self._stop_manual_motion("button_release"))
        manual_buttons.addWidget(self.manual_down_button, 1)
        manual_layout.addLayout(manual_buttons)
        self.manual_position_value = QLabel("Current pivot position: unavailable")
        self.manual_position_value.setObjectName("hardwareInfoValue")
        self.manual_position_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        manual_layout.addWidget(self.manual_position_value)
        self.manual_safety_note = QLabel(
            "Hold a direction to move; release to stop. Firmware dead-man default: 350 ms; validate during commissioning."
        )
        self.manual_safety_note.setObjectName("subtle")
        self.manual_safety_note.setWordWrap(True)
        manual_layout.addWidget(self.manual_safety_note)
        layout.addWidget(manual_panel)

        latch_panel = QFrame()
        latch_panel.setObjectName("surface")
        self._apply_card_depth(latch_panel)
        latch_layout = QVBoxLayout(latch_panel)
        latch_layout.setContentsMargins(16, 12, 16, 12)
        latch_layout.setSpacing(8)
        latch_header = QHBoxLayout()
        latch_heading = QLabel("PENDULUM LATCH")
        latch_heading.setObjectName("sectionTitle")
        latch_header.addWidget(latch_heading)
        latch_header.addStretch(1)
        self.latch_status_button = QPushButton("Read state")
        self.latch_status_button.setObjectName("latchStatusButton")
        self.latch_status_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.latch_status_button.setToolTip("Read the firmware's commanded latch state")
        self.latch_status_button.clicked.connect(self._request_latch_status)
        latch_header.addWidget(self.latch_status_button)
        latch_layout.addLayout(latch_header)

        self.latch_state_value = QLabel(PendulumLatchState.UNKNOWN.value)
        self.latch_state_value.setObjectName("pendulumLatchState")
        self.latch_state_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        latch_layout.addWidget(self.latch_state_value)
        self.latch_message_value = QLabel("No latch command state has been confirmed.")
        self.latch_message_value.setObjectName("subtle")
        self.latch_message_value.setWordWrap(True)
        latch_layout.addWidget(self.latch_message_value)

        latch_buttons = QHBoxLayout()
        latch_buttons.setSpacing(12)
        self.latch_hold_button = QPushButton("HOLD")
        self.latch_hold_button.setObjectName("latchHoldButton")
        self.latch_hold_button.clicked.connect(
            lambda: self._command_latch(PendulumLatchState.HOLD)
        )
        self.latch_open_button = QPushButton("OPEN")
        self.latch_open_button.setObjectName("latchOpenButton")
        self.latch_open_button.clicked.connect(
            lambda: self._command_latch(PendulumLatchState.OPEN)
        )
        latch_buttons.addWidget(self.latch_hold_button, 1)
        latch_buttons.addWidget(self.latch_open_button, 1)
        latch_layout.addLayout(latch_buttons)

        self.latch_commissioning_note = QLabel(
            "Position is manually calibrated. The servo holds or opens the pendulum latch; "
            "it does not set the pendulum initial angle. State is command-only, with no position feedback."
        )
        self.latch_commissioning_note.setObjectName("subtle")
        self.latch_commissioning_note.setWordWrap(True)
        latch_layout.addWidget(self.latch_commissioning_note)
        layout.addWidget(latch_panel)
        layout.addStretch(1)

        controller.state.snapshot_changed.connect(self._update_application_state)
        controller.state.telemetry_received.connect(self._update_manual_position)
        self.maintenance.snapshot_changed.connect(self._update_maintenance_snapshot)
        self._update_application_state(controller.state.snapshot)
        self._update_maintenance_snapshot(self.maintenance.snapshot)
        QApplication.instance().applicationStateChanged.connect(self._on_application_state_changed)

    @staticmethod
    def _apply_card_depth(card: QFrame) -> None:
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(36, 56, 47, 24))
        card.setGraphicsEffect(shadow)

    @staticmethod
    def _diagnostic_value(title: str) -> QLabel:
        label = QLabel("Unknown")
        label.setObjectName("hardwareInfoValue")
        label.setToolTip(title)
        return label

    def _run_system_check(self) -> None:
        try:
            self.maintenance.run_system_check()
        except (RuntimeError, ValueError) as error:
            self.home_progress_value.setText(str(error))

    def _clear_fault(self) -> None:
        self.clear_fault_result_value.setText("Waiting for firmware fault validation...")
        try:
            self.maintenance.clear_fault()
        except (RuntimeError, ValueError) as error:
            self.clear_fault_result_value.setText(str(error))

    def _start_homing(self) -> None:
        try:
            self.maintenance.start_homing()
        except (RuntimeError, ValueError) as error:
            self.home_progress_value.setText(str(error))

    def _start_manual_motion(self, direction: str) -> None:
        try:
            self.maintenance.start_manual_motion(direction)
        except (RuntimeError, ValueError) as error:
            self.manual_motion_state_value.setText(str(error))
            self.controller.state.report_event(EventSeverity.WARNING, str(error))

    def _stop_manual_motion(self, reason: str) -> None:
        if self.maintenance.is_manual_motion_active:
            self.maintenance.stop_manual_motion(reason)

    def _command_latch(self, state: PendulumLatchState) -> None:
        try:
            self.maintenance.command_latch(state)
        except (RuntimeError, ValueError) as error:
            self.latch_message_value.setText(f"Latch command blocked: {error}")
            self.controller.state.report_event(EventSeverity.WARNING, str(error))

    def _request_latch_status(self) -> None:
        try:
            self.maintenance.request_latch_status()
        except (RuntimeError, ValueError) as error:
            self.latch_message_value.setText(f"Latch status unavailable: {error}")

    def _on_application_state_changed(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            self._stop_manual_motion("application_focus_lost")

    def _confirm_angle_zero(self) -> None:
        angle = self.maintenance.snapshot.current_angle_deg
        angle_text = "unavailable" if angle is None else f"{angle:+.2f} deg"
        result = QMessageBox.question(
            self,
            "Set Angle Sensor Zero",
            f"Set the current pendulum angle ({angle_text}) as the new zero reference?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        try:
            self.zero_result_value.setText("Waiting for firmware confirmation...")
            self.maintenance.set_angle_zero()
        except (RuntimeError, ValueError) as error:
            self.zero_result_value.setText(str(error))

    def _update_application_state(self, snapshot: ApplicationSnapshot) -> None:
        connected = snapshot.connection == ConnectionState.CONNECTED
        maintenance = self.maintenance.snapshot
        busy = maintenance.operation is not None or self.controller.firmware_commands.is_busy
        recording_active = snapshot.recording in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        )
        blocking_fault = snapshot.hardware_status.fault_known and bool(snapshot.hardware_status.fault)
        moving = snapshot.motion in (MotionState.RUNNING, MotionState.STOPPING)
        readiness = self.maintenance.snapshot.readiness
        manual_ready = (
            connected
            and readiness == ReadinessState.SYSTEM_READY
            and not busy
            and not recording_active
            and not moving
            and not blocking_fault
        )
        self.run_system_check_button.setEnabled(connected and not busy)
        self.clear_fault_button.setEnabled(
            connected
            and blocking_fault
            and not busy
            and not recording_active
            and not moving
            and not self.maintenance.is_manual_motion_active
            and snapshot.motion != MotionState.HOMING
            and snapshot.hardware_status.active_motion_mode.strip().lower() == "idle"
        )
        home_otherwise_enabled = (
            connected and not busy and not blocking_fault and not recording_active and not moving
        )
        self.home_button.setEnabled(
            home_otherwise_enabled
            and maintenance.latch_state == PendulumLatchState.OPEN
        )
        self.home_latch_interlock_note.setVisible(
            connected and maintenance.latch_state != PendulumLatchState.OPEN
        )
        self.zero_angle_button.setEnabled(
            connected
            and not busy
            and not recording_active
            and snapshot.motion not in (MotionState.RUNNING, MotionState.HOMING, MotionState.STOPPING)
        )
        manual_active = self.maintenance.is_manual_motion_active
        direction = self.maintenance.snapshot.manual_motion
        self.manual_up_button.setEnabled(
            manual_active and direction in (ManualMotionState.STARTING_UP, ManualMotionState.MOVING_UP)
            or (manual_ready and snapshot.hardware_status.upper_limit is False)
        )
        self.manual_down_button.setEnabled(
            manual_active and direction in (ManualMotionState.STARTING_DOWN, ManualMotionState.MOVING_DOWN)
            or (manual_ready and snapshot.hardware_status.lower_limit is False)
        )
        latch_ready = (
            connected
            and not busy
            and not recording_active
            and snapshot.motion not in (MotionState.RUNNING, MotionState.HOMING, MotionState.STOPPING)
            and not blocking_fault
            and snapshot.hardware_status.active_motion_mode.strip().lower() == "idle"
        )
        self.latch_hold_button.setEnabled(latch_ready)
        self.latch_open_button.setEnabled(latch_ready)
        self.latch_status_button.setEnabled(connected and not busy)
        self.home_state_value.setText(snapshot.home_status.value)
        self.upper_limit_value.setText(self._limit_text(snapshot.hardware_status.upper_limit))
        self.lower_limit_value.setText(self._limit_text(snapshot.hardware_status.lower_limit))

    def _update_maintenance_snapshot(self, snapshot: MaintenanceSnapshot) -> None:
        self.readiness_indicator.setText(snapshot.readiness.value)
        foreground, background = self._READINESS_COLORS[snapshot.readiness]
        self.readiness_indicator.setStyleSheet(
            f"color: {foreground}; background: {background}; padding: 8px 10px; font-weight: 700;"
        )
        detail = {
            ReadinessState.NOT_CONNECTED: "Connect to the STM32 to begin maintenance checks.",
            ReadinessState.CHECK_REQUIRED: "Run the power-up checks to evaluate system readiness.",
            ReadinessState.BIT_PASSED_HOME_REQUIRED: "Checks passed; home the pivot before experiment motion is available.",
            ReadinessState.SYSTEM_READY: "Firmware reports homed and the current-session BIT passed.",
            ReadinessState.FAULT: "A required check, homing operation, or firmware fault requires attention.",
        }[snapshot.readiness]
        self.readiness_detail.setText(detail)
        self.bit_result_value.setText(snapshot.bit_state.value)
        if snapshot.operation == "fault_clear" or snapshot.operation_message.startswith(
            ("Cannot clear fault:", "Fault cleared;", "No active firmware fault;")
        ):
            self.clear_fault_result_value.setText(snapshot.operation_message)
        for result in snapshot.checks:
            state_label, detail_label = self.check_rows[result.name]
            state_label.setText(result.state.value)
            color, fill = self._CHECK_COLORS[result.state]
            state_label.setStyleSheet(
                f"color: {color}; background: {fill}; padding: 3px 5px; font-weight: 700;"
            )
            detail_label.setText(result.detail or "Not checked")
        if snapshot.latest_check_at is None:
            self.bit_timestamp_value.setText("No completed check")
        else:
            timestamp = snapshot.latest_check_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            self.bit_timestamp_value.setText(f"Last check: {timestamp}")
        if snapshot.operation is not None:
            self.home_progress_value.setText(snapshot.operation_message)
        elif snapshot.operation_message:
            self.home_progress_value.setText(snapshot.operation_message)
        self.home_state_value.setText(self.controller.state.snapshot.home_status.value)
        self.travel_span_value.setText(self._travel_text(snapshot.travel_span_steps, snapshot.steps_per_mm))
        self.pivot_position_value.setText(self._position_text(snapshot.position_steps, snapshot.position_mm))
        if snapshot.current_angle_deg is None:
            self.current_angle_value.setText("Current angle: unavailable")
        else:
            self.current_angle_value.setText(f"Current angle: {snapshot.current_angle_deg:+.2f} deg")
        if snapshot.operation == "zero":
            self.zero_result_value.setText(snapshot.operation_message)
        elif snapshot.operation_message and self.zero_result_value.text().startswith("Waiting"):
            self.zero_result_value.setText(snapshot.operation_message)
        self.manual_motion_state_value.setText(snapshot.manual_motion.value)
        self.manual_motion_state_value.setToolTip(snapshot.manual_motion_message)
        self.manual_up_button.setText(
            "MOVING UP" if snapshot.manual_motion == ManualMotionState.MOVING_UP else "MOVE UP"
        )
        self.manual_down_button.setText(
            "MOVING DOWN" if snapshot.manual_motion == ManualMotionState.MOVING_DOWN else "MOVE DOWN"
        )
        for button, is_active in (
            (self.manual_up_button, snapshot.manual_motion == ManualMotionState.MOVING_UP),
            (self.manual_down_button, snapshot.manual_motion == ManualMotionState.MOVING_DOWN),
        ):
            button.setProperty("manualActive", is_active)
            button.style().unpolish(button)
            button.style().polish(button)
        self.latch_state_value.setText(snapshot.latch_state.value)
        self.latch_state_value.setToolTip(snapshot.latch_message)
        self.latch_message_value.setText(snapshot.latch_message)
        self._update_application_state(self.controller.state.snapshot)

    def _update_manual_position(self, sample) -> None:
        if sample.source != "real" or sample.position_mm is None:
            return
        self.manual_position_value.setText(f"Current pivot position: {sample.position_mm:+.2f} mm")

    def hideEvent(self, event) -> None:
        self._stop_manual_motion("maintenance_page_left")
        super().hideEvent(event)

    @staticmethod
    def _limit_text(active: bool | None) -> str:
        if active is None:
            return "Unknown"
        return "ACTIVE" if active else "INACTIVE"

    @staticmethod
    def _travel_text(steps: int | None, steps_per_mm: float | None) -> str:
        if steps is None:
            return "Unknown"
        if steps_per_mm is None or steps_per_mm <= 0:
            return f"{steps} steps"
        return f"{steps} steps ({steps / steps_per_mm:.2f} mm)"

    @staticmethod
    def _position_text(steps: int | None, position_mm: float | None) -> str:
        if steps is None and position_mm is None:
            return "Unknown"
        if position_mm is None:
            return f"{steps} steps"
        if steps is None:
            return f"{position_mm:+.2f} mm"
        return f"{position_mm:+.2f} mm ({steps} steps)"