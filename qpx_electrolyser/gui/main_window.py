"""Compose guarded simulation/real connection and manual channel controls."""

from pathlib import Path

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser import __version__
from qpx_electrolyser.app.channel_controller import ManualChannelController
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionSnapshot
from qpx_electrolyser.gui.channel_panel import ChannelPanel
from qpx_electrolyser.gui.connection_panel import ConnectionPanel


class MainWindow(QMainWindow):
    """Compose widgets and route intent; never call the backend directly."""

    def __init__(self, log_file: Path, connection_controller: ConnectionController) -> None:
        super().__init__()
        self._connection_controller = connection_controller
        self.setWindowTitle("QPX600DP Electrolyser - Guarded manual control")
        self.resize(1120, 720)
        self.setMinimumSize(760, 540)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)

        title = QLabel("Aim-TTi QPX600DP | Electrolyser testing")
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        self.connection_panel = ConnectionPanel()
        self.connection_panel.connect_requested.connect(connection_controller.request_connect)
        self.connection_panel.disconnect_requested.connect(connection_controller.request_disconnect)
        self.connection_panel.simulation_mode_changed.connect(
            connection_controller.set_simulation_mode
        )
        self.connection_panel.port_name_changed.connect(connection_controller.set_port_name)
        connection_controller.snapshot_changed.connect(self._apply_connection_snapshot)
        connection_controller.error_reported.connect(self._show_connection_error)
        self.connection_panel.apply_snapshot(connection_controller.snapshot)
        layout.addWidget(self.connection_panel)

        panels = QHBoxLayout()
        panels.setSpacing(18)
        self.channel_controllers: dict[int, ManualChannelController] = {}
        self.channel_panels: dict[int, ChannelPanel] = {}
        for channel in (1, 2):
            controller = ManualChannelController(channel, connection_controller, self)
            panel = ChannelPanel(channel)
            panel.refresh_requested.connect(controller.request_refresh)
            panel.voltage_requested.connect(controller.request_set_voltage)
            panel.current_requested.connect(controller.request_set_current)
            panel.output_on_requested.connect(controller.request_output_on)
            panel.output_off_requested.connect(controller.request_output_off)
            controller.snapshot_changed.connect(panel.apply_snapshot)
            panel.apply_snapshot(controller.snapshot)
            self.channel_controllers[channel] = controller
            self.channel_panels[channel] = panel
            panels.addWidget(panel, 1)
        layout.addLayout(panels, 1)

        notice = QLabel(
            "Simulation is the default. Real Hardware requires deliberate mode selection, a "
            "manually entered COM port and explicit Connect; ports are never enumerated."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Disconnected | Simulation available | Diagnostic logging active")
        self.statusBar().setToolTip(str(log_file))

        file_menu = self.menuBar().addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About QPX600DP Electrolyser",
            f"QPX600DP Electrolyser {__version__}\n\n"
            "Milestone 7 guarded dual-channel manual-control build.\n\n"
            "The QPX600DP is a programmable DC power supply, not a potentiostat.\n"
            "Simulation is the default. Real Hardware uses only a manually entered COM port. "
            "Automated protocols, logging, plotting and emergency-all-off remain future work.",
        )

    def _apply_connection_snapshot(self, snapshot: ConnectionSnapshot) -> None:
        self.connection_panel.apply_snapshot(snapshot)
        for panel in self.channel_panels.values():
            panel.set_real_hardware_mode(not snapshot.simulation_mode)
        self.statusBar().showMessage(snapshot.message)

    def _show_connection_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Connection problem", message)
