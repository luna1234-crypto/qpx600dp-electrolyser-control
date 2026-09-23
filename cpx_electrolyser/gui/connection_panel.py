"""Milestone 4 connection controls; this widget performs no backend calls."""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from cpx_electrolyser.app.connection_controller import ConnectionSnapshot, ConnectionState


class ConnectionPanel(QGroupBox):
    """Collect connection intent and render immutable controller snapshots."""

    connect_requested = Signal()
    disconnect_requested = Signal()
    simulation_mode_changed = Signal(bool)

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
        super().__init__("Instrument connection")
        self.simulation_checkbox = QCheckBox("Simulation Mode (no hardware I/O)")
        self.simulation_checkbox.setObjectName("simulationModeCheckBox")
        self.simulation_checkbox.setChecked(True)
        self.connect_button = QPushButton("Connect")
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

        controls = QHBoxLayout()
        controls.addWidget(self.simulation_checkbox)
        controls.addStretch(1)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.disconnect_button)

        information = QGridLayout()
        information.addWidget(QLabel("Status:"), 0, 0)
        information.addWidget(self.status_indicator, 0, 1)
        information.addWidget(QLabel("Instrument identification:"), 1, 0)
        information.addWidget(self.identification_value, 1, 1)
        information.setColumnStretch(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addLayout(information)
        layout.addWidget(self.detail_label)

        self.connect_button.clicked.connect(self.connect_requested)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.simulation_checkbox.toggled.connect(self.simulation_mode_changed)

    @Slot(object)
    def apply_snapshot(self, snapshot: ConnectionSnapshot) -> None:
        busy_or_connected = snapshot.state in {
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
            ConnectionState.DISCONNECTING,
        }
        can_connect = (
            snapshot.simulation_mode
            and snapshot.state in {ConnectionState.DISCONNECTED, ConnectionState.ERROR}
        )
        self.simulation_checkbox.blockSignals(True)
        self.simulation_checkbox.setChecked(snapshot.simulation_mode)
        self.simulation_checkbox.blockSignals(False)
        self.simulation_checkbox.setEnabled(not busy_or_connected)
        self.connect_button.setEnabled(can_connect)
        self.disconnect_button.setEnabled(snapshot.state is ConnectionState.CONNECTED)
        self.status_indicator.setText(self._STATE_LABELS[snapshot.state])
        colour = self._STATE_COLOURS[snapshot.state]
        self.status_indicator.setStyleSheet(f"font-weight: 600; color: {colour};")
        self.identification_value.setText(snapshot.instrument_id or "Unavailable")
        self.detail_label.setText(snapshot.message)
