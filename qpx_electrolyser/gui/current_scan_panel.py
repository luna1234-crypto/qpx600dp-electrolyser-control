"""Per-channel Milestone 13 Simulation Mode current-scan controls."""

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QDoubleValidator
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


class CurrentScanPanel(QGroupBox):
    start_requested = Signal(str, str, str, str, str, str)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Current Scan")
        self.channel = channel
        self._available = False
        self._simulation_mode = True
        self._snapshot = ProtocolSnapshot(channel=channel, message="Current Scan idle")

        self.start_current_input = self._input("0.1", f"channel{channel}ScanStartCurrent")
        self.stop_current_input = self._input("0.3", f"channel{channel}ScanStopCurrent")
        self.step_input = self._input(
            "0.1", f"channel{channel}ScanCurrentStep", signed=True
        )
        self.voltage_compliance_input = self._input(
            "2", f"channel{channel}ScanVoltageCompliance"
        )
        self.dwell_input = self._input("1", f"channel{channel}CurrentScanDwell")
        self.interval_input = self._input("0.25", f"channel{channel}CurrentScanInterval")

        inputs = QGridLayout()
        labels = (
            ("Start current (A):", self.start_current_input),
            ("Stop current (A):", self.stop_current_input),
            ("Current step (A):", self.step_input),
            ("Voltage compliance (V):", self.voltage_compliance_input),
            ("Dwell per step (s):", self.dwell_input),
            ("Sampling interval (s):", self.interval_input),
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

        self.message_label = QLabel("Current Scan idle")
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName(f"channel{channel}CurrentScanMessage")

        layout = QVBoxLayout(self)
        layout.addLayout(inputs)
        layout.addLayout(buttons)
        layout.addLayout(status)
        layout.addWidget(self.message_label)

        self.start_button.clicked.connect(lambda: self.start_requested.emit(
            self.start_current_input.text(),
            self.stop_current_input.text(),
            self.step_input.text(),
            self.voltage_compliance_input.text(),
            self.dwell_input.text(),
            self.interval_input.text(),
        ))
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.apply_snapshot(self._snapshot)

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
            else f"{snapshot.scan_commanded_setpoint:g} A"
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
            self.message_label.setText("Milestone 13 real-hardware acceptance pending")
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
            self.message_label.setText("Milestone 13 real-hardware acceptance pending")
        self._update_enabled()

    def _update_enabled(self) -> None:
        state = self._snapshot.state
        start_ready = (
            self._available
            and self._simulation_mode
            and state in {ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED}
        )
        for widget in (
            self.start_current_input,
            self.stop_current_input,
            self.step_input,
            self.voltage_compliance_input,
            self.dwell_input,
            self.interval_input,
        ):
            widget.setEnabled(start_ready)
        self.start_button.setEnabled(start_ready)
        self.pause_button.setEnabled(state is ProtocolState.RUNNING)
        self.resume_button.setEnabled(
            state is ProtocolState.PAUSED
            and self._snapshot.resume_ready
            and self._available
            and self._simulation_mode
        )
        self.stop_button.setEnabled(state not in {
            ProtocolState.IDLE,
            ProtocolState.COMPLETED,
            ProtocolState.FAILED,
            ProtocolState.STOPPING,
        })
