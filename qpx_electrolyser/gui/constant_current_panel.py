"""Per-channel Constant Current protocol controls and status."""

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from qpx_electrolyser.engine.constant_current import ProtocolSnapshot, ProtocolState
from qpx_electrolyser.engine.current_input import CurrentInputMode, CurrentInputSelection


class ConstantCurrentPanel(QGroupBox):
    start_requested = Signal(str, str, str, str, str, str)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    plot_style_changed = Signal(str)

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Chronopotentiometry (CP)")
        self.channel = channel
        self._available = False
        self._snapshot = ProtocolSnapshot(channel=channel)
        self._start_validation_message: str | None = None

        self.current_input = self._input("A", f"channel{channel}ProtocolCurrent")
        self.input_mode = QComboBox()
        self.input_mode.setObjectName(f"channel{channel}ProtocolCurrentMode")
        self.input_mode.addItem("Current (A)", CurrentInputMode.CURRENT.value)
        self.input_mode.addItem(
            "Current density (A/cm²)", CurrentInputMode.CURRENT_DENSITY.value
        )
        self.active_area_input = self._input(
            "cm²", f"channel{channel}ProtocolActiveArea"
        )
        self.active_area_input.setText("1")
        self.voltage_input = self._input("V", f"channel{channel}ProtocolVoltage")
        self.duration_input = self._input("s", f"channel{channel}ProtocolDuration")
        self.interval_input = self._input("s", f"channel{channel}ProtocolInterval")
        self.current_input.setText("0.1")
        self.voltage_input.setText("2")
        self.duration_input.setText("10")
        self.interval_input.setText("1")
        self.plot_style = QComboBox()
        self.plot_style.setObjectName(f"channel{channel}CpPlotStyle")
        self.plot_style.addItem("Dots", "dots")
        self.plot_style.addItem("Line + Dots", "line_dots")
        self.plot_style.setToolTip(
            "Display-only CP live-plot style; measurements and CSV data are unchanged."
        )

        inputs = QGridLayout()
        self.input_mode_label = QLabel("Current input mode:")
        inputs.addWidget(self.input_mode_label, 0, 0)
        inputs.addWidget(self.input_mode, 0, 1)
        self.current_label = QLabel("Target current (A):")
        self.voltage_label = QLabel("Voltage limit / compliance (V):")
        inputs.addWidget(self.current_label, 1, 0)
        inputs.addWidget(self.current_input, 1, 1)
        self.active_area_label = QLabel("Active area (cm²):")
        inputs.addWidget(self.active_area_label, 2, 0)
        inputs.addWidget(self.active_area_input, 2, 1)
        self.derived_current_value = QLabel("0.1 A")
        self.derived_current_value.setObjectName(f"channel{channel}ProtocolDerivedCurrent")
        self.derived_current_label = QLabel("Derived current:")
        inputs.addWidget(self.derived_current_label, 3, 0)
        inputs.addWidget(self.derived_current_value, 3, 1)
        inputs.addWidget(self.voltage_label, 4, 0)
        inputs.addWidget(self.voltage_input, 4, 1)
        inputs.addWidget(QLabel("Duration (s):"), 5, 0)
        inputs.addWidget(self.duration_input, 5, 1)
        inputs.addWidget(QLabel("Interval (s):"), 6, 0)
        inputs.addWidget(self.interval_input, 6, 1)
        inputs.addWidget(QLabel("CP live plot:"), 7, 0)
        inputs.addWidget(self.plot_style, 7, 1)

        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        buttons = QHBoxLayout()
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            buttons.addWidget(button)

        self.state_value = QLabel("IDLE")
        self.elapsed_value = QLabel("0.000 s")
        self.remaining_value = QLabel("0.000 s")
        self.voltage_value = QLabel("Unavailable")
        self.current_value = QLabel("Unavailable")
        self.output_state_value = QLabel("UNKNOWN")
        self.output_state_value.setObjectName(f"channel{channel}ProtocolOutputState")
        self.missed_value = QLabel("0")
        status = QGridLayout()
        status.addWidget(QLabel("State:"), 0, 0)
        status.addWidget(self.state_value, 0, 1)
        status.addWidget(QLabel("Output state:"), 0, 2)
        status.addWidget(self.output_state_value, 0, 3)
        status.addWidget(QLabel("Elapsed active:"), 1, 0)
        status.addWidget(self.elapsed_value, 1, 1)
        status.addWidget(QLabel("Remaining active:"), 1, 2)
        status.addWidget(self.remaining_value, 1, 3)
        status.addWidget(QLabel("Latest voltage:"), 2, 0)
        status.addWidget(self.voltage_value, 2, 1)
        status.addWidget(QLabel("Latest current:"), 2, 2)
        status.addWidget(self.current_value, 2, 3)
        status.addWidget(QLabel("Missed samples:"), 3, 0)
        status.addWidget(self.missed_value, 3, 1)
        status.setColumnStretch(1, 1)
        status.setColumnStretch(3, 1)

        self.message_label = QLabel("Constant Current protocol idle")
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName(f"channel{channel}ProtocolMessage")

        layout = QVBoxLayout(self)
        layout.addLayout(inputs)
        layout.addLayout(buttons)
        layout.addLayout(status)
        layout.addWidget(self.message_label)

        self.start_button.clicked.connect(
            lambda: self.start_requested.emit(
                self.current_input.text(),
                self.voltage_input.text(),
                self.duration_input.text(),
                self.interval_input.text(),
                str(self.input_mode.currentData()),
                self.active_area_input.text(),
            )
        )
        self.input_mode.currentIndexChanged.connect(self._current_mode_changed)
        self.current_input.textChanged.connect(self._update_derived_current)
        self.active_area_input.textChanged.connect(self._update_derived_current)
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.plot_style.currentIndexChanged.connect(
            lambda _index: self.plot_style_changed.emit(str(self.plot_style.currentData()))
        )
        self._current_mode_changed()
        self.apply_snapshot(self._snapshot)

    def set_plot_style(self, style: str) -> None:
        index = self.plot_style.findData(style)
        self.plot_style.setCurrentIndex(index if index >= 0 else 0)

    def _current_mode_changed(self) -> None:
        density = self.input_mode.currentData() == CurrentInputMode.CURRENT_DENSITY.value
        self.current_label.setText(
            "Target current density (A/cm²):" if density else "Target current (A):"
        )
        self.active_area_label.setVisible(density)
        self.active_area_input.setVisible(density)
        self._update_derived_current()

    def _update_derived_current(self) -> None:
        try:
            resolved = CurrentInputSelection.parse(
                str(self.input_mode.currentData()), self.active_area_input.text()
            ).resolve(self.current_input.text(), "Target current density")
            self.derived_current_value.setText(f"{float(resolved.current_a):g} A")
            self.derived_current_value.setStyleSheet("")
        except ValueError as error:
            self.derived_current_value.setText(str(error))
            self.derived_current_value.setStyleSheet("color: #a61b1b;")

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

    @staticmethod
    def _input(placeholder: str, object_name: str) -> QLineEdit:
        widget = QLineEdit()
        widget.setPlaceholderText(placeholder)
        widget.setObjectName(object_name)
        widget.setValidator(QDoubleValidator(0.0, 86_400.0, 6))
        return widget

    @Slot(object)
    def apply_snapshot(self, snapshot: ProtocolSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self._snapshot = snapshot
        self.state_value.setText(snapshot.state.value)
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
        self.message_label.setText(snapshot.message)
        if snapshot.operator_warning:
            self.message_label.setStyleSheet(
                "font-weight: 800; color: white; background: #b42318; padding: 4px;"
            )
        else:
            self.message_label.setStyleSheet("color: #a61b1b;" if snapshot.error else "")
        self._update_enabled()

    def set_context(self, *, available: bool) -> None:
        self._available = available
        self._update_enabled()

    def _update_enabled(self) -> None:
        state = self._snapshot.state
        editable = (
            self._available
            and state in {ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED}
        )
        for widget in (
            self.current_input,
            self.input_mode,
            self.active_area_input,
            self.voltage_input,
            self.duration_input,
            self.interval_input,
        ):
            widget.setEnabled(editable)
        # Plot styling is display-only and remains available during a run.
        self.plot_style.setEnabled(True)
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
