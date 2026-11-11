"""Guarded simulation/real connection controls; no backend calls occur here."""

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser.app.connection_controller import ConnectionSnapshot, ConnectionState


class ModeOptionCard(QRadioButton):
    """High-contrast, fully clickable mode choice with native radio semantics."""

    def __init__(self, label: str, *, role: str, warning_badge: bool = False) -> None:
        super().__init__(label)
        self.role = role
        self.warning_badge = warning_badge
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(48)
        self.setMinimumWidth(190 if warning_badge else 142)
        self.last_paint_state: dict[str, object] = {}
        self.setAccessibleName(label)
        self.toggled.connect(self._sync_accessible_state)
        self._sync_accessible_state(self.isChecked())

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(max(self.minimumWidth(), hint.width()), max(48, hint.height()))

    def _sync_accessible_state(self, checked: bool) -> None:
        self.setProperty("selectedState", "selected" if checked else "unselected")
        suffix = " Hardware warning." if self.warning_badge else ""
        self.setAccessibleDescription(
            f"{self.text()} is {'selected' if checked else 'not selected'}.{suffix}"
        )
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self.window().property("uiTheme") == "dark"
        checked = self.isChecked()
        enabled = self.isEnabled()
        accent = QColor("#0f6cbd" if self.role == "simulation" else "#ca5010")
        neutral = QColor("#8a8886" if not dark else "#9aa2ad")
        text = QColor("#242424" if not dark else "#f3f4f6")
        if not enabled:
            accent = QColor("#8a8a8a")
            neutral = QColor("#777777" if dark else "#b5b5b5")
            text = QColor("#8f8f8f" if not dark else "#858b94")
        selected_fill = QColor(
            "#e6f2fb" if self.role == "simulation" and not dark else
            "#173a54" if self.role == "simulation" else
            "#fff1d6" if not dark else "#493714"
        )
        self.last_paint_state = {
            "theme": "dark" if dark else "light",
            "selected": checked,
            "accent": accent.name(),
            "selected_fill": selected_fill.name(),
            "warning_badge": self.warning_badge,
            "enabled": enabled,
        }
        rectangle = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(selected_fill if checked else Qt.BrushStyle.NoBrush)
        border = accent if checked or self.underMouse() or self.hasFocus() else neutral
        painter.setPen(QPen(border, 2 if checked or self.hasFocus() else 1))
        painter.drawRoundedRect(rectangle, 6, 6)

        centre_x = 16
        centre_y = self.height() // 2
        painter.setPen(QPen(accent if checked else neutral, 2))
        painter.setBrush(accent if checked else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre_x - 9, centre_y - 9, 18, 18)
        if checked:
            painter.setPen(QPen(QColor("#ffffff"), 2.2, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(centre_x - 4, centre_y, centre_x - 1, centre_y + 4)
            painter.drawLine(centre_x - 1, centre_y + 4, centre_x + 5, centre_y - 4)

        painter.setPen(text)
        label_font = painter.font()
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.drawText(32, 5, max(40, self.width() - 38), 22,
                         Qt.AlignmentFlag.AlignVCenter, self.text())
        small_font = painter.font()
        small_font.setBold(False)
        small_font.setPointSizeF(max(7.5, small_font.pointSizeF() - 1.5))
        painter.setFont(small_font)
        painter.setPen(accent if checked else neutral)
        painter.drawText(32, 25, max(40, self.width() - 38), 18,
                         Qt.AlignmentFlag.AlignVCenter,
                         "Selected" if checked else "Not selected")
        if self.warning_badge:
            badge_text = "Hardware"
            metrics = painter.fontMetrics()
            badge_width = metrics.horizontalAdvance(badge_text) + 12
            badge = self.rect().adjusted(
                self.width() - badge_width - 7, 6,
                -7, -(self.height() - 23),
            )
            badge_colour = QColor("#ca5010" if enabled else "#777777")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(badge_colour)
            painter.drawRoundedRect(badge, 7, 7)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_text)
        painter.end()


class ConnectionPanel(QGroupBox):
    """Collect explicit connection intent and render immutable controller state."""

    connect_requested = Signal()
    disconnect_requested = Signal()
    simulation_mode_changed = Signal(bool)
    port_name_changed = Signal(str)

    _STATE_LABELS = {
        ConnectionState.DISCONNECTED: "Disconnected",
        ConnectionState.CONNECTING: "Connecting...",
        ConnectionState.CONNECTED: "Connected",
        ConnectionState.DISCONNECTING: "Disconnecting...",
        ConnectionState.ERROR: "Connection error",
    }
    _STATE_COLOURS = {
        ConnectionState.DISCONNECTED: "#666666",
        ConnectionState.CONNECTING: "#a86600",
        ConnectionState.CONNECTED: "#16733a",
        ConnectionState.DISCONNECTING: "#a86600",
        ConnectionState.ERROR: "#a61b1b",
    }

    def __init__(self) -> None:
        super().__init__("")
        self.setObjectName("commandBarConnection")
        self.setFlat(True)
        self._applying_snapshot = False
        self.simulation_checkbox = ModeOptionCard("Simulation", role="simulation")
        self.simulation_checkbox.setToolTip("Simulation Mode — no hardware I/O")
        self.simulation_checkbox.setObjectName("simulationModeRadio")
        self.simulation_checkbox.setChecked(True)
        self.real_hardware_radio = ModeOptionCard(
            "Real Hardware", role="hardware", warning_badge=True
        )
        self.real_hardware_radio.setToolTip("Guarded Real Hardware — explicit manual port")
        self.real_hardware_radio.setObjectName("realHardwareModeRadio")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.simulation_checkbox)
        self._mode_group.addButton(self.real_hardware_radio)

        self.mode_indicator = QLabel("SIMULATION - NO HARDWARE I/O")
        self.mode_indicator.setObjectName("connectionModeIndicator")
        self.port_input = QLineEdit()
        self.port_input.setObjectName("manualPortInput")
        self.port_input.setPlaceholderText("COM port, for example COM7")
        self.connect_button = QPushButton("Connect Simulator")
        self.connect_button.setObjectName("connectButton")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("disconnectButton")
        self.status_indicator = QLabel()
        self.status_indicator.setObjectName("connectionStatusIndicator")
        self.identification_value = QLabel("Unavailable")
        self.identification_value.setObjectName("instrumentIdentification")
        self.identification_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.detail_label = QLabel()
        self.detail_label.setObjectName("connectionDetail")
        self.detail_label.setWordWrap(True)

        modes = QHBoxLayout()
        modes.addWidget(self.simulation_checkbox)
        modes.addWidget(self.real_hardware_radio)
        modes.addStretch(1)
        modes.addWidget(self.mode_indicator)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Port:"))
        controls.addWidget(self.port_input, 1)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.disconnect_button)

        information = QGridLayout()
        information.addWidget(QLabel("Instrument identification:"), 0, 0)
        information.addWidget(self.identification_value, 0, 1)
        information.setColumnStretch(1, 1)

        self.advanced_widget = QWidget()
        self.advanced_widget.setObjectName("advancedConnectionDetails")
        advanced_layout = QVBoxLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(0, 4, 0, 0)
        advanced_layout.addLayout(information)
        advanced_layout.addWidget(self.detail_label)
        self.advanced_widget.hide()
        self.advanced_button = QPushButton("Connection details")
        self.advanced_button.setObjectName("advancedConnectionButton")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self.advanced_widget.setVisible)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        controls.addWidget(self.status_indicator)
        controls.addWidget(self.advanced_button)
        layout.addLayout(modes)
        layout.addLayout(controls)
        layout.addWidget(self.advanced_widget)

        self.connect_button.clicked.connect(self.connect_requested)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.simulation_checkbox.toggled.connect(self._simulation_toggled)
        self.real_hardware_radio.toggled.connect(self._real_hardware_toggled)
        self.port_input.textChanged.connect(self.port_name_changed)

    @Slot(bool)
    def _simulation_toggled(self, checked: bool) -> None:
        if checked and not self._applying_snapshot:
            self.simulation_mode_changed.emit(True)

    @Slot(bool)
    def _real_hardware_toggled(self, checked: bool) -> None:
        if not checked or self._applying_snapshot:
            return
        response = QMessageBox.warning(
            self,
            "Enter guarded Real Hardware mode?",
            "Real Hardware mode can send commands to a power supply after Connect is pressed.\n\n"
            "No port will be opened now. You must manually enter a COM port and press "
            "Connect Real Hardware before any access occurs.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Yes:
            self.simulation_mode_changed.emit(False)
            return
        self._applying_snapshot = True
        self.simulation_checkbox.setChecked(True)
        self._applying_snapshot = False

    @Slot(object)
    def apply_snapshot(self, snapshot: ConnectionSnapshot) -> None:
        idle = snapshot.state in {ConnectionState.DISCONNECTED, ConnectionState.ERROR}
        self._applying_snapshot = True
        self.simulation_checkbox.setChecked(snapshot.simulation_mode)
        self.real_hardware_radio.setChecked(not snapshot.simulation_mode)
        if self.port_input.text() != snapshot.port_name:
            self.port_input.blockSignals(True)
            self.port_input.setText(snapshot.port_name)
            self.port_input.blockSignals(False)
        self._applying_snapshot = False

        self.simulation_checkbox.setEnabled(idle)
        self.real_hardware_radio.setEnabled(idle)
        self.port_input.setEnabled(idle and not snapshot.simulation_mode)
        can_connect = idle and (snapshot.simulation_mode or snapshot.real_port_valid)
        self.connect_button.setEnabled(can_connect)
        self.connect_button.setText(
            "Connect Simulator" if snapshot.simulation_mode else "Connect Real Hardware"
        )
        self.disconnect_button.setEnabled(snapshot.state is ConnectionState.CONNECTED)
        self.mode_indicator.setText(snapshot.mode_label)
        if snapshot.simulation_mode:
            self.mode_indicator.setStyleSheet(
                "font-weight: 800; color: white; background: #0f6cbd; padding: 4px;"
            )
        else:
            self.mode_indicator.setStyleSheet(
                "font-weight: 800; color: white; background: #ca5010; padding: 4px;"
            )
        self.status_indicator.setText(self._STATE_LABELS[snapshot.state])
        colour = self._STATE_COLOURS[snapshot.state]
        self.status_indicator.setStyleSheet(f"font-weight: 600; color: {colour};")
        self.identification_value.setText(snapshot.instrument_id or "Unavailable")
        self.detail_label.setText(snapshot.message)
