"""Milestone 1 window composition only. Controls arrive in later milestones."""

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

from cpx_electrolyser import __version__


def _channel_placeholder(channel: int) -> QGroupBox:
    panel = QGroupBox(f"Channel {channel}")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(24, 28, 24, 24)
    layout.addWidget(QLabel("No experiment running"))
    layout.addWidget(QLabel("Output state: unknown (not connected)"))
    detail = QLabel("Instrument control, measurements and test protocols are not available in this build.")
    detail.setWordWrap(True)
    detail.setAlignment(Qt.AlignmentFlag.AlignTop)
    layout.addWidget(detail)
    layout.addStretch(1)
    return panel


class MainWindow(QMainWindow):
    """Display a clearly labelled offline foundation without functional controls."""

    def __init__(self, log_file: Path) -> None:
        super().__init__()
        self.setWindowTitle("CPX400DP Electrolyser - Project skeleton")
        self.resize(1040, 590)
        self.setMinimumSize(760, 460)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)

        title = QLabel("AIM-TTi CPX400DP | Electrolyser testing")
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        connection = QGroupBox("Instrument")
        connection_layout = QVBoxLayout(connection)
        connection_layout.addWidget(QLabel("Not connected | No commands are sent by this build"))
        connection_layout.addWidget(QLabel("Instrument identification: unavailable"))
        layout.addWidget(connection)

        panels = QHBoxLayout()
        panels.setSpacing(18)
        for channel in (1, 2):
            panels.addWidget(_channel_placeholder(channel), 1)
        layout.addLayout(panels, 1)

        notice = QLabel("Project skeleton only. This build cannot start, pause or stop a power supply.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Offline | Diagnostic logging active")
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
            "About CPX400DP Electrolyser",
            f"CPX400DP Electrolyser {__version__}\n\n"
            "Offline application skeleton.\n\n"
            "The CPX400DP is a programmable DC power supply, not a potentiostat.\n"
            "This build has no instrument connection or experiment controls.",
        )
