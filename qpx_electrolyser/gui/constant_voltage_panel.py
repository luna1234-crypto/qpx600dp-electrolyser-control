"""Per-channel Constant Voltage protocol controls and status."""

from PySide6.QtCore import Signal

from qpx_electrolyser.engine.constant_current import ProtocolSnapshot
from qpx_electrolyser.gui.constant_current_panel import ConstantCurrentPanel


class ConstantVoltagePanel(ConstantCurrentPanel):
    """Reuse the protocol status widget with CV-specific labels and input order."""

    start_requested = Signal(str, str, str, str)

    def __init__(self, channel: int) -> None:
        super().__init__(channel)
        self.setTitle(f"Channel {channel} Chronoamperometry (CA)")
        self.target_voltage_input = self.voltage_input
        self.current_limit_input = self.current_input
        self.target_voltage_input.setObjectName(f"channel{channel}CvTargetVoltage")
        self.current_limit_input.setObjectName(f"channel{channel}CvCurrentLimit")
        self.target_voltage_input.setText("2")
        self.current_limit_input.setText("0.1")
        self.current_label.setText("Current limit (A):")
        self.voltage_label.setText("Target voltage (V):")
        self.message_label.setText("Constant Voltage protocol idle")
        for widget in (
            self.input_mode_label,
            self.input_mode,
            self.active_area_label,
            self.active_area_input,
            self.derived_current_label,
            self.derived_current_value,
        ):
            widget.hide()

        self.start_button.clicked.disconnect()
        self.start_button.clicked.connect(
            lambda: self.start_requested.emit(
                self.target_voltage_input.text(),
                self.current_limit_input.text(),
                self.duration_input.text(),
                self.interval_input.text(),
            )
        )
        self.apply_snapshot(
            ProtocolSnapshot(channel=channel, message="Constant Voltage protocol idle")
        )
