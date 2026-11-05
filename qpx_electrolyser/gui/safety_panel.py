"""M16 channel safety-limit editor and prominent global emergency action."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser.safety.model import SafetyLimits, SafetyValidator


class SafetyPanel(QWidget):
    limits_changed = Signal(int, object)
    emergency_requested = Signal()

    def __init__(self, validator: SafetyValidator) -> None:
        super().__init__()
        self._inputs: dict[int, tuple[QDoubleSpinBox, ...]] = {}
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Unified Safety Features"))
        heading.addStretch(1)
        self.emergency_button = QPushButton("EMERGENCY ALL OUTPUTS OFF")
        self.emergency_button.setObjectName("emergencyAllOffButton")
        self.emergency_button.setStyleSheet(
            "QPushButton { background: #b42318; color: white; font-weight: 900; "
            "padding: 10px; border: 2px solid #7a1712; }"
        )
        self.emergency_button.clicked.connect(self.emergency_requested)
        heading.addWidget(self.emergency_button)
        layout.addLayout(heading)
        channels = QHBoxLayout()
        for channel in (1, 2):
            limits = validator.user_limits(channel)
            box = QGroupBox(f"CH{channel} user safety limits")
            grid = QGridLayout(box)
            values = (
                ("Maximum voltage (V):", limits.max_voltage_v, 80.0),
                ("Maximum current (A):", limits.max_current_a, 50.0),
                ("Maximum power (W):", limits.max_power_w, 600.0),
                ("Maximum current density (A/cm²):", limits.max_current_density_a_cm2, 1000.0),
            )
            widgets: list[QDoubleSpinBox] = []
            for row, (label, value, maximum) in enumerate(values):
                field = QDoubleSpinBox()
                field.setDecimals(4)
                field.setRange(0.0001, maximum)
                field.setValue(value)
                field.setObjectName(f"channel{channel}SafetyLimit{row}")
                grid.addWidget(QLabel(label), row, 0)
                grid.addWidget(field, row, 1)
                widgets.append(field)
            apply_button = QPushButton(f"Apply CH{channel} limits")
            apply_button.clicked.connect(
                lambda _checked=False, selected=channel: self._emit_limits(selected)
            )
            grid.addWidget(apply_button, len(values), 0, 1, 2)
            self._inputs[channel] = tuple(widgets)
            channels.addWidget(box)
        layout.addLayout(channels)
        self.status_label = QLabel(
            "Effective limits are the strictest of user, project and QPX600DP limits."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _emit_limits(self, channel: int) -> None:
        voltage, current, power, density = self._inputs[channel]
        self.limits_changed.emit(channel, SafetyLimits(
            voltage.value(), current.value(), power.value(), density.value()
        ))

    def apply_limits(self, channel: int, limits: SafetyLimits) -> None:
        """Synchronize the editor after a persisted/default policy is applied."""
        for field, value in zip(self._inputs[channel], (
            limits.max_voltage_v,
            limits.max_current_a,
            limits.max_power_w,
            limits.max_current_density_a_cm2,
        )):
            field.setValue(value)

    def show_message(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #b42318;" if error else "")

    def set_emergency_pending(self, pending: bool) -> None:
        self.emergency_button.setEnabled(not pending)
        self.emergency_button.setText(
            "EMERGENCY ALL-OFF IN PROGRESS..."
            if pending else "EMERGENCY ALL OUTPUTS OFF"
        )
