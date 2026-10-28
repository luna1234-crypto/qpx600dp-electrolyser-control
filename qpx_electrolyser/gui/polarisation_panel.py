"""Reusable CH1/CH2 steady-state polarisation controls."""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from qpx_electrolyser.engine.constant_current import ProtocolSnapshot, ProtocolState
from qpx_electrolyser.engine.current_input import CurrentInputMode, CurrentInputSelection
from qpx_electrolyser.engine.polarisation import PolarisationPoint


class PolarisationPanel(QGroupBox):
    start_requested = Signal(str, str, str, str, str, str, str, str)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Steady-State Polarisation")
        self.channel = channel
        self._available = False
        self._simulation_mode = True
        self._snapshot = ProtocolSnapshot(channel=channel, message="Polarisation idle")
        self.active_area_input = self._input("1", f"channel{channel}PolarisationArea")
        self.start_density_input = self._input("0.01", f"channel{channel}PolarisationStartDensity")
        self.stop_density_input = self._input("0.03", f"channel{channel}PolarisationStopDensity")
        self.increment_input = self._input("0.01", f"channel{channel}PolarisationIncrement", signed=True)
        self.voltage_input = self._input("4", f"channel{channel}PolarisationVoltage")
        self.dwell_input = self._input("1", f"channel{channel}PolarisationDwell")
        self.interval_input = self._input("0.1", f"channel{channel}PolarisationInterval")
        self.averaging_input = self._input("0.3", f"channel{channel}PolarisationAveraging")
        grid = QGridLayout()
        fields = (
            ("Active area (cm²):", self.active_area_input),
            ("Start current density (A/cm²):", self.start_density_input),
            ("End current density (A/cm²):", self.stop_density_input),
            ("Signed increment (A/cm²):", self.increment_input),
            ("Voltage compliance (V):", self.voltage_input),
            ("Dwell per step (s):", self.dwell_input),
            ("Sampling interval (s):", self.interval_input),
            ("Final averaging window (s):", self.averaging_input),
        )
        for row, (label, widget) in enumerate(fields):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(widget, row, 1)
        self.derived_value = QLabel("Derived currents unavailable")
        self.derived_value.setWordWrap(True)
        grid.addWidget(QLabel("Derived current plan:"), len(fields), 0)
        grid.addWidget(self.derived_value, len(fields), 1)

        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        buttons = QHBoxLayout()
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            buttons.addWidget(button)

        self.state_value = QLabel("IDLE")
        self.progress_value = QLabel("Unavailable")
        self.latest_value = QLabel("Unavailable")
        self.mean_value = QLabel("Unavailable")
        self.summary_path_value = QLabel("Unavailable")
        self.summary_path_value.setWordWrap(True)
        status = QGridLayout()
        for row, (label, widget) in enumerate((
            ("State:", self.state_value),
            ("Progress:", self.progress_value),
            ("Latest V / I:", self.latest_value),
            ("Latest mean V / population SD:", self.mean_value),
            ("Raw + summary CSV:", self.summary_path_value),
        )):
            status.addWidget(QLabel(label), row, 0)
            status.addWidget(widget, row, 1)
        self.message_label = QLabel(
            "Power-supply polarisation only; this is not potentiostat control."
        )
        self.message_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addLayout(buttons)
        layout.addLayout(status)
        layout.addWidget(self.message_label)

        self.start_button.clicked.connect(lambda: self.start_requested.emit(
            self.active_area_input.text(), self.start_density_input.text(),
            self.stop_density_input.text(), self.increment_input.text(),
            self.voltage_input.text(), self.dwell_input.text(),
            self.interval_input.text(), self.averaging_input.text(),
        ))
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        for widget in (self.active_area_input, self.start_density_input,
                       self.stop_density_input, self.increment_input):
            widget.textChanged.connect(self._update_derived)
        self._update_derived()
        self.apply_snapshot(self._snapshot)

    @staticmethod
    def _input(value: str, name: str, *, signed: bool = False) -> QLineEdit:
        widget = QLineEdit(value)
        widget.setObjectName(name)
        widget.setValidator(QDoubleValidator(-86400 if signed else 0, 86400, 9))
        return widget

    def _update_derived(self) -> None:
        try:
            selection = CurrentInputSelection.parse(
                CurrentInputMode.CURRENT_DENSITY.value, self.active_area_input.text()
            )
            start = selection.resolve(self.start_density_input.text(), "Start density").current_a
            stop = selection.resolve(self.stop_density_input.text(), "End density").current_a
            step = selection.resolve(self.increment_input.text(), "Density increment").current_a
            self.derived_value.setText(
                f"{float(start):g} → {float(stop):g} A; increment {float(step):g} A"
            )
            self.derived_value.setStyleSheet("")
        except ValueError as error:
            self.derived_value.setText(str(error))
            self.derived_value.setStyleSheet("color: #a61b1b;")

    @Slot(object)
    def apply_snapshot(self, snapshot: ProtocolSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self._snapshot = snapshot
        self.state_value.setText(snapshot.state.value)
        self.progress_value.setText(
            "Unavailable" if snapshot.scan_step_index is None
            else f"step {snapshot.scan_step_index}/{snapshot.scan_total_steps}; "
                 f"active {snapshot.elapsed_active_s:.3f} s, remaining {snapshot.remaining_active_s:.3f} s"
        )
        self.latest_value.setText(
            "Unavailable" if snapshot.measured_voltage_v is None else
            f"{snapshot.measured_voltage_v:g} V / {snapshot.measured_current_a:g} A"
        )
        self.summary_path_value.setText(
            "Unavailable"
            if snapshot.summary_file_path is None
            else str(snapshot.summary_file_path)
        )
        if snapshot.error or snapshot.operator_warning:
            self.message_label.setText(snapshot.message)
            self.message_label.setStyleSheet("color: #a61b1b; font-weight: 700;")
        elif not self._simulation_mode and not snapshot.owns_channel:
            self.message_label.setText(
                "Guarded Real Hardware power-supply polarisation available; physical acceptance pending."
            )
            self.message_label.setStyleSheet("")
        else:
            self.message_label.setText(
                f"{snapshot.message} — power-supply polarisation, not potentiostat control."
            )
            self.message_label.setStyleSheet("")
        self._update_enabled()

    @Slot(object)
    def apply_point(self, point: object) -> None:
        if not isinstance(point, PolarisationPoint) or point.channel != self.channel:
            return
        self.mean_value.setText(
            f"{point.mean_voltage_v:g} V / {point.population_voltage_sd_v:g} V "
            f"({point.sample_count} samples)"
        )

    def set_context(self, *, available: bool, simulation_mode: bool) -> None:
        self._available = available
        self._simulation_mode = simulation_mode
        self._update_enabled()

    def show_validation_error(self, message: str) -> None:
        self.message_label.setText(message)
        self.message_label.setStyleSheet("color: #a61b1b;")

    def _update_enabled(self) -> None:
        state = self._snapshot.state
        start_ready = self._available and state in {
            ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED,
        }
        for widget in (
            self.active_area_input, self.start_density_input, self.stop_density_input,
            self.increment_input, self.voltage_input, self.dwell_input,
            self.interval_input, self.averaging_input,
        ):
            widget.setEnabled(start_ready)
        self.start_button.setEnabled(start_ready)
        self.pause_button.setEnabled(state is ProtocolState.RUNNING)
        self.resume_button.setEnabled(
            state is ProtocolState.PAUSED and self._snapshot.resume_ready and self._available
        )
        self.stop_button.setEnabled(state not in {
            ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED,
            ProtocolState.STOPPING,
        })
