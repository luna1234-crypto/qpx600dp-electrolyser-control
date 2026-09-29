"""Guarded simulation/real connection controls; no backend calls occur here."""

from PySide6.QtCore import Qt, Signal, Slot
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
)

from qpx_electrolyser.app.connection_controller import ConnectionSnapshot, ConnectionState


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
        super().__init__("Instrument connection")
        self._applying_snapshot = False
        self.simulation_checkbox = QRadioButton("Simulation Mode (no hardware I/O)")
        self.simulation_checkbox.setObjectName("simulationModeRadio")
        self.simulation_checkbox.setChecked(True)
        self.real_hardware_radio = QRadioButton("Real Hardware (explicit port, guarded)")
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
        controls.addWidget(QLabel("Manual port:"))
        controls.addWidget(self.port_input, 1)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.disconnect_button)

        information = QGridLayout()
        information.addWidget(QLabel("Status:"), 0, 0)
        information.addWidget(self.status_indicator, 0, 1)
        information.addWidget(QLabel("Instrument identification:"), 1, 0)
        information.addWidget(self.identification_value, 1, 1)
        information.setColumnStretch(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(modes)
        layout.addLayout(controls)
        layout.addLayout(information)
        layout.addWidget(self.detail_label)

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
                "font-weight: 800; color: white; background: #16733a; padding: 4px;"
            )
        else:
            self.mode_indicator.setStyleSheet(
                "font-weight: 800; color: white; background: #b42318; padding: 4px;"
            )
        self.status_indicator.setText(self._STATE_LABELS[snapshot.state])
        colour = self._STATE_COLOURS[snapshot.state]
        self.status_indicator.setStyleSheet(f"font-weight: 600; color: {colour};")
        self.identification_value.setText(snapshot.instrument_id or "Unavailable")
        self.detail_label.setText(snapshot.message)
