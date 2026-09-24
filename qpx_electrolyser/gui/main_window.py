"""Compose the milestone 4 simulation connection GUI and channel placeholders."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser import __version__
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionSnapshot
from qpx_electrolyser.gui.connection_panel import ConnectionPanel


def _channel_placeholder(channel: int) -> QGroupBox:
    panel = QGroupBox(f"Channel {channel}")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(24, 28, 24, 24)
    layout.addWidget(QLabel("No experiment running"))
    layout.addWidget(QLabel("Output state: unknown (not queried in this milestone)"))
    detail = QLabel("Instrument control, measurements and test protocols are not available in this build.")
    detail.setWordWrap(True)
    detail.setAlignment(Qt.AlignmentFlag.AlignTop)
    layout.addWidget(detail)
    layout.addStretch(1)
    return panel


class MainWindow(QMainWindow):
    """Compose widgets and route intent; never call the backend directly."""

    def __init__(self, log_file: Path, connection_controller: ConnectionController) -> None:
        super().__init__()
        self._connection_controller = connection_controller
        self.setWindowTitle("QPX600DP Electrolyser - Simulation connection")
        self.resize(1040, 650)
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
        connection_controller.snapshot_changed.connect(self._apply_connection_snapshot)
        connection_controller.error_reported.connect(self._show_connection_error)
        self.connection_panel.apply_snapshot(connection_controller.snapshot)
        layout.addWidget(self.connection_panel)

        panels = QHBoxLayout()
        panels.setSpacing(18)
        for channel in (1, 2):
            panels.addWidget(_channel_placeholder(channel), 1)
        layout.addLayout(panels, 1)

        notice = QLabel(
            "Milestone 4 connects only to the simulator. This build cannot set voltage/current, "
            "switch an output, or connect to physical hardware."
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
            "Milestone 4 simulation connection GUI.\n\n"
            "The QPX600DP is a programmable DC power supply, not a potentiostat.\n"
            "This build has no physical driver or output/experiment controls.",
        )

    def _apply_connection_snapshot(self, snapshot: ConnectionSnapshot) -> None:
        self.connection_panel.apply_snapshot(snapshot)
        self.statusBar().showMessage(snapshot.message)

    def _show_connection_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Connection problem", message)
