"""Per-channel Milestone 12 Voltage Sweep / Staircase LSV controls."""

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from qpx_electrolyser.engine.constant_current import ProtocolSnapshot, ProtocolState


class VoltageScanPanel(QGroupBox):
    start_requested = Signal(str, str, str, str, str, str, str, str, str)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Voltage Sweep / Staircase LSV")
        self.channel = channel
        self._available = False
        self._simulation_mode = True
        self._snapshot = ProtocolSnapshot(channel=channel, message="Staircase LSV idle")
        self._start_validation_message: str | None = None

        self.start_voltage_input = self._input("2", f"channel{channel}ScanStartVoltage")
        self.stop_voltage_input = self._input("3", f"channel{channel}ScanStopVoltage")
        self.step_input = self._input("500", f"channel{channel}ScanVoltageStepMv", signed=True)
        self.scan_rate_input = self._input("1250", f"channel{channel}ScanRateMvPerSecond")
        self.current_limit_input = self._input("0.1", f"channel{channel}ScanCurrentLimit")
        self.settling_input = self._input("0.1", f"channel{channel}ScanSettling")
        self.measurements_input = QLineEdit("2")
        self.measurements_input.setObjectName(f"channel{channel}ScanMeasurementsPerPoint")
        self.measurements_input.setValidator(QIntValidator(1, 1_000))
        self.interval_input = self._input("0.1", f"channel{channel}ScanInterval")
        self.active_area_input = self._input("", f"channel{channel}ScanActiveArea")
        self.derived_duration_value = QLabel("0.400 s")
        self.derived_duration_value.setObjectName(f"channel{channel}ScanDerivedDuration")

        inputs = QGridLayout()
        labels = (
            ("Start voltage (V):", self.start_voltage_input),
            ("End voltage (V):", self.stop_voltage_input),
            ("Signed step size (mV):", self.step_input),
            ("Scan rate (mV/s):", self.scan_rate_input),
            ("Derived step duration:", self.derived_duration_value),
            ("Current limit (A):", self.current_limit_input),
            ("Settling per point (s):", self.settling_input),
            ("Measurements per point:", self.measurements_input),
            ("Sampling interval (s):", self.interval_input),
            ("Active area (cm², optional):", self.active_area_input),
        )
        for row, (label, widget) in enumerate(labels):
            inputs.addWidget(QLabel(label), row, 0)
            inputs.addWidget(widget, row, 1)

        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        buttons = QHBoxLayout()
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            buttons.addWidget(button)

        self.state_value = QLabel("IDLE")
        self.step_value = QLabel("Unavailable")
        self.commanded_value = QLabel("Unavailable")
        self.elapsed_value = QLabel("0.000 s")
        self.remaining_value = QLabel("0.000 s")
        self.voltage_value = QLabel("Unavailable")
        self.current_value = QLabel("Unavailable")
        self.output_state_value = QLabel("UNKNOWN")
        self.missed_value = QLabel("0")
        status = QGridLayout()
        fields = (
            ("State:", self.state_value, "Step:", self.step_value),
            ("Commanded:", self.commanded_value, "Output:", self.output_state_value),
            ("Elapsed active:", self.elapsed_value, "Remaining:", self.remaining_value),
            ("Latest voltage:", self.voltage_value, "Latest current:", self.current_value),
            ("Missed samples:", self.missed_value, "", QLabel("")),
        )
        for row, (left_label, left_value, right_label, right_value) in enumerate(fields):
            status.addWidget(QLabel(left_label), row, 0)
            status.addWidget(left_value, row, 1)
            if right_label:
                status.addWidget(QLabel(right_label), row, 2)
                status.addWidget(right_value, row, 3)

        self.message_label = QLabel("Voltage Scan idle")
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName(f"channel{channel}VoltageScanMessage")

        layout = QVBoxLayout(self)
        layout.addLayout(inputs)
        layout.addLayout(buttons)
        layout.addLayout(status)
        layout.addWidget(self.message_label)

        self.start_button.clicked.connect(lambda: self.start_requested.emit(
            self.start_voltage_input.text(),
            self.stop_voltage_input.text(),
            self.step_input.text(),
            self.scan_rate_input.text(),
            self.current_limit_input.text(),
            self.settling_input.text(),
            self.measurements_input.text(),
            self.interval_input.text(),
            self.active_area_input.text(),
        ))
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.step_input.textChanged.connect(self._update_derived_duration)
        self.scan_rate_input.textChanged.connect(self._update_derived_duration)
        self.apply_snapshot(self._snapshot)

    @Slot()
    def _update_derived_duration(self) -> None:
        try:
            step = Decimal(self.step_input.text().strip())
            rate = Decimal(self.scan_rate_input.text().strip())
            if not step.is_finite() or not rate.is_finite() or step == 0 or rate <= 0:
                raise InvalidOperation
            self.derived_duration_value.setText(f"{abs(step) / rate:.6f} s")
        except (InvalidOperation, ValueError):
            self.derived_duration_value.setText("Invalid")

    @staticmethod
    def _input(value: str, object_name: str, *, signed: bool = False) -> QLineEdit:
        widget = QLineEdit(value)
        widget.setObjectName(object_name)
        widget.setValidator(QDoubleValidator(-86_400.0 if signed else 0.0, 86_400.0, 6))
        return widget

    @Slot(object)
    def apply_snapshot(self, snapshot: ProtocolSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self._snapshot = snapshot
        self.state_value.setText(snapshot.state.value)
        self.step_value.setText(
            "Unavailable"
            if snapshot.scan_step_index is None or snapshot.scan_total_steps is None
            else f"{snapshot.scan_step_index}/{snapshot.scan_total_steps}"
        )
        self.commanded_value.setText(
            "Unavailable"
            if snapshot.scan_commanded_setpoint is None
            else f"{snapshot.scan_commanded_setpoint:g} V"
        )
        self.elapsed_value.setText(f"{snapshot.elapsed_active_s:.3f} s")
        self.remaining_value.setText(f"{snapshot.remaining_active_s:.3f} s")
        self.voltage_value.setText(
            "Unavailable" if snapshot.measured_voltage_v is None
            else f"{snapshot.measured_voltage_v:g} V"
        )
        self.current_value.setText(
            "Unavailable" if snapshot.measured_current_a is None
            else f"{snapshot.measured_current_a:g} A"
        )
        if snapshot.output_enabled is True:
            self.output_state_value.setText("OUTPUT ON")
            self.output_state_value.setStyleSheet(
                "font-weight: 800; color: white; background: #b42318; padding: 2px;"
            )
        elif snapshot.output_enabled is False:
            self.output_state_value.setText("OUTPUT OFF")
            self.output_state_value.setStyleSheet(
                "font-weight: 800; color: white; background: #16733a; padding: 2px;"
            )
        else:
            self.output_state_value.setText("UNKNOWN")
            self.output_state_value.setStyleSheet(
                "font-weight: 800; color: white; background: #666666; padding: 2px;"
            )
        self.missed_value.setText(str(snapshot.missed_samples))
        if not self._simulation_mode and not snapshot.owns_channel:
            self.message_label.setText(
                "Revised Real Hardware Staircase LSV acceptance pending"
            )
        else:
            self.message_label.setText(snapshot.message)
        self.message_label.setStyleSheet(
            "font-weight: 800; color: white; background: #b42318; padding: 4px;"
            if snapshot.operator_warning else ("color: #a61b1b;" if snapshot.error else "")
        )
        self._update_enabled()

    def set_context(self, *, available: bool, simulation_mode: bool) -> None:
        self._available = available
        self._simulation_mode = simulation_mode
        if not simulation_mode and not self._snapshot.owns_channel:
            self.message_label.setText(
                "Revised Real Hardware Staircase LSV acceptance pending"
            )
        self._update_enabled()

    def show_validation_error(self, message: str) -> None:
        self._start_validation_message = message
        self.message_label.setText(message)
        self.message_label.setStyleSheet("color: #a61b1b;")
        self.start_button.setToolTip(message)
        self._update_enabled()

    def set_start_validation(self, message: str | None) -> None:
        previous = self._start_validation_message
        self._start_validation_message = message
        self.start_button.setToolTip(message or "")
        if message:
            self.message_label.setText(message)
            self.message_label.setStyleSheet("color: #a61b1b;")
        elif previous:
            self.message_label.setText(self._snapshot.message)
            self.message_label.setStyleSheet(
                "color: #a61b1b;" if self._snapshot.error else ""
            )
        self._update_enabled()

    def _update_enabled(self) -> None:
        state = self._snapshot.state
        editable = (
            self._available
            and state in {ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED}
        )
        for widget in (
            self.start_voltage_input,
            self.stop_voltage_input,
            self.step_input,
            self.scan_rate_input,
            self.current_limit_input,
            self.settling_input,
            self.measurements_input,
            self.interval_input,
            self.active_area_input,
        ):
            widget.setEnabled(editable)
        self.start_button.setEnabled(
            editable and self._start_validation_message is None
        )
        self.pause_button.setEnabled(state is ProtocolState.RUNNING)
        self.resume_button.setEnabled(
            state is ProtocolState.PAUSED
            and self._snapshot.resume_ready
            and self._available
        )
        self.stop_button.setEnabled(state not in {
            ProtocolState.IDLE,
            ProtocolState.COMPLETED,
            ProtocolState.FAILED,
            ProtocolState.STOPPING,
        })
