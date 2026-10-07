"""Reusable manual channel controls with no backend or worker ownership."""

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from qpx_electrolyser.app.channel_controller import ChannelSnapshot
from qpx_electrolyser.data.csv_logging import LoggingSnapshot
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES


class ChannelPanel(QGroupBox):
    refresh_requested = Signal()
    voltage_requested = Signal(str)
    current_requested = Signal(str)
    output_on_requested = Signal(bool)
    output_off_requested = Signal()
    start_logging_requested = Signal()
    stop_logging_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel}")
        QPX600DP_CAPABILITIES.validate_channel(channel)
        self.channel = channel
        self._real_hardware = False
        self._snapshot = ChannelSnapshot(channel=channel)
        self._logging_snapshot = LoggingSnapshot(channel=channel)
        self._protocol_owned = False

        self.programmed_voltage_value = QLabel("Unavailable")
        self.current_limit_value = QLabel("Unavailable")
        self.measured_voltage_value = QLabel("Unavailable")
        self.measured_current_value = QLabel("Unavailable")
        self.voltage_range_value = QLabel("Unavailable")
        self.output_state_value = QLabel("UNKNOWN")
        self.output_state_value.setObjectName(f"channel{channel}OutputState")
        self.output_state_value.setMinimumHeight(26)

        readings = QGridLayout()
        readings.addWidget(QLabel("Programmed voltage:"), 0, 0)
        readings.addWidget(self.programmed_voltage_value, 0, 1)
        readings.addWidget(QLabel("Current limit:"), 0, 2)
        readings.addWidget(self.current_limit_value, 0, 3)
        readings.addWidget(QLabel("Measured voltage:"), 1, 0)
        readings.addWidget(self.measured_voltage_value, 1, 1)
        readings.addWidget(QLabel("Measured current:"), 1, 2)
        readings.addWidget(self.measured_current_value, 1, 3)
        readings.addWidget(QLabel("Voltage range:"), 2, 0)
        readings.addWidget(self.voltage_range_value, 2, 1)
        readings.addWidget(QLabel("Output state:"), 2, 2)
        readings.addWidget(self.output_state_value, 2, 3)
        readings.setColumnStretch(1, 1)
        readings.setColumnStretch(3, 1)

        self.voltage_input = QLineEdit()
        self.voltage_input.setObjectName(f"channel{channel}VoltageInput")
        self.voltage_input.setPlaceholderText("V")
        self.voltage_input.setValidator(QDoubleValidator(0, QPX600DP_CAPABILITIES.max_voltage_v, 6))
        self.set_voltage_button = QPushButton("Set voltage")

        self.current_input = QLineEdit()
        self.current_input.setObjectName(f"channel{channel}CurrentInput")
        self.current_input.setPlaceholderText("A")
        self.current_input.setValidator(QDoubleValidator(
            0, QPX600DP_CAPABILITIES.max_current_a, 6
        ))
        self.set_current_button = QPushButton("Set current limit")

        setters = QGridLayout()
        setters.addWidget(self.voltage_input, 0, 0)
        setters.addWidget(self.set_voltage_button, 0, 1)
        setters.addWidget(self.current_input, 1, 0)
        setters.addWidget(self.set_current_button, 1, 1)

        self.refresh_button = QPushButton("Refresh")
        self.output_on_button = QPushButton("Output On...")
        self.output_off_button = QPushButton("Output Off")
        outputs = QHBoxLayout()
        outputs.addWidget(self.refresh_button)
        outputs.addStretch(1)
        outputs.addWidget(self.output_on_button)
        outputs.addWidget(self.output_off_button)

        self.message_label = QLabel("Disconnected")
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName(f"channel{channel}Message")

        self.start_logging_button = QPushButton("Start Logging")
        self.start_logging_button.setObjectName(f"channel{channel}StartLogging")
        self.stop_logging_button = QPushButton("Stop Logging")
        self.stop_logging_button.setObjectName(f"channel{channel}StopLogging")
        logging_buttons = QHBoxLayout()
        logging_buttons.addWidget(self.start_logging_button)
        logging_buttons.addWidget(self.stop_logging_button)
        self.logging_status_label = QLabel("Logging stopped")
        self.logging_status_label.setWordWrap(True)
        self.logging_status_label.setObjectName(f"channel{channel}LoggingStatus")

        layout = QVBoxLayout(self)
        layout.addLayout(readings)
        layout.addSpacing(8)
        layout.addLayout(setters)
        layout.addLayout(outputs)
        layout.addWidget(self.message_label)
        layout.addSpacing(8)
        layout.addLayout(logging_buttons)
        layout.addWidget(self.logging_status_label)
        layout.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh_requested)
        self.set_voltage_button.clicked.connect(
            lambda: self.voltage_requested.emit(self.voltage_input.text())
        )
        self.set_current_button.clicked.connect(
            lambda: self.current_requested.emit(self.current_input.text())
        )
        self.output_on_button.clicked.connect(self._confirm_output_on)
        self.output_off_button.clicked.connect(self.output_off_requested)
        self.start_logging_button.clicked.connect(self.start_logging_requested)
        self.stop_logging_button.clicked.connect(self.stop_logging_requested)
        self.apply_snapshot(ChannelSnapshot(channel=channel))
        self.apply_logging_snapshot(LoggingSnapshot(channel=channel))

    @Slot(object)
    def apply_snapshot(self, snapshot: ChannelSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self._snapshot = snapshot
        available = snapshot.connected and not snapshot.pending and not self._protocol_owned
        for widget in (
            self.voltage_input,
            self.current_input,
            self.set_voltage_button,
            self.set_current_button,
            self.output_on_button,
            self.output_off_button,
        ):
            widget.setEnabled(available and not snapshot.faulted)
        self.refresh_button.setEnabled(available)

        self.programmed_voltage_value.setText(self._quantity(snapshot.programmed_voltage_v, "V"))
        self.current_limit_value.setText(self._quantity(snapshot.current_limit_a, "A"))
        self.measured_voltage_value.setText(self._quantity(snapshot.measured_voltage_v, "V"))
        self.measured_current_value.setText(self._quantity(snapshot.measured_current_a, "A"))
        self.voltage_range_value.setText(snapshot.voltage_range_name or "Unavailable")
        if snapshot.output_enabled is True:
            self.output_state_value.setText("OUTPUT ON")
            self.output_state_value.setStyleSheet(
                "font-weight: 800; color: white; background: #b42318; padding: 4px;"
            )
        elif snapshot.output_enabled is False:
            self.output_state_value.setText("OUTPUT OFF")
            self.output_state_value.setStyleSheet(
                "font-weight: 800; color: white; background: #16733a; padding: 4px;"
            )
        else:
            self.output_state_value.setText("UNKNOWN")
            self.output_state_value.setStyleSheet(
                "font-weight: 800; color: white; background: #666666; padding: 4px;"
            )
        self.message_label.setText(snapshot.message)
        self.message_label.setStyleSheet("color: #a61b1b;" if snapshot.error else "")
        self._update_logging_buttons()

    @Slot(object)
    def apply_logging_snapshot(self, snapshot: LoggingSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self._logging_snapshot = snapshot
        detail = snapshot.message
        if snapshot.active:
            detail += (
                f" | samples={snapshot.sample_count}, missed={snapshot.missed_samples}"
            )
        self.logging_status_label.setText(detail)
        self.logging_status_label.setToolTip(
            "" if snapshot.file_path is None else str(snapshot.file_path)
        )
        self.logging_status_label.setStyleSheet("color: #a61b1b;" if snapshot.error else "")
        self._update_logging_buttons()

    def _update_logging_buttons(self) -> None:
        self.start_logging_button.setEnabled(
            self._snapshot.connected and not self._logging_snapshot.active and not self._protocol_owned
        )
        self.stop_logging_button.setEnabled(self._logging_snapshot.active)

    @Slot(bool)
    def set_protocol_owned(self, owned: bool) -> None:
        self._protocol_owned = owned
        self.apply_snapshot(self._snapshot)
        self._update_logging_buttons()

    @Slot(bool)
    def set_real_hardware_mode(self, enabled: bool) -> None:
        self._real_hardware = enabled

    @Slot()
    def _confirm_output_on(self) -> None:
        if self._real_hardware:
            voltage = self._quantity(self._snapshot.programmed_voltage_v, "V")
            current = self._quantity(self._snapshot.current_limit_a, "A")
            response = QMessageBox.warning(
                self,
                f"Confirm REAL Channel {self.channel} Output On",
                "REAL HARDWARE OUTPUT ENABLE\n\n"
                f"Channel: {self.channel}\n"
                f"Programmed voltage: {voltage}\n"
                f"Current limit: {current}\n\n"
                "Confirm the load and physical setup are safe before enabling this output.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
        else:
            response = QMessageBox.question(
                self,
                f"Confirm Channel {self.channel} Output On",
                f"Enable simulated Channel {self.channel} output at the displayed setpoints?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        if response == QMessageBox.StandardButton.Yes:
            self.output_on_requested.emit(True)

    @staticmethod
    def _quantity(value: float | None, unit: str) -> str:
        return "Unavailable" if value is None else f"{value:g} {unit}"
