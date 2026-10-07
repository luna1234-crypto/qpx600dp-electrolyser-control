"""Compose guarded simulation/real connection and manual channel controls."""

from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser import __version__
from qpx_electrolyser.app.channel_controller import ManualChannelController
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionSnapshot
from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.data.csv_logging import ChannelLoggingController
from qpx_electrolyser.engine.constant_current import (
    ConstantCurrentController,
    ProtocolSnapshot,
)
from qpx_electrolyser.gui.channel_panel import ChannelPanel
from qpx_electrolyser.gui.connection_panel import ConnectionPanel
from qpx_electrolyser.gui.constant_current_panel import ConstantCurrentPanel


class MainWindow(QMainWindow):
    """Compose widgets and route intent; never call the backend directly."""

    def __init__(
        self,
        log_file: Path,
        connection_controller: ConnectionController,
        *,
        data_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self._connection_controller = connection_controller
        self.setWindowTitle("QPX600DP Electrolyser - Constant Current protocols")
        self.resize(1180, 940)
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
        self.connection_panel.disconnect_requested.connect(self._request_disconnect)
        self.connection_panel.simulation_mode_changed.connect(
            connection_controller.set_simulation_mode
        )
        self.connection_panel.port_name_changed.connect(connection_controller.set_port_name)
        connection_controller.snapshot_changed.connect(self._apply_connection_snapshot)
        connection_controller.error_reported.connect(self._show_connection_error)
        self.connection_panel.apply_snapshot(connection_controller.snapshot)
        layout.addWidget(self.connection_panel)

        logging_row = QHBoxLayout()
        logging_row.addWidget(QLabel("CSV output directory:"))
        self._data_directory = Path(data_directory or default_paths().data_directory).resolve()
        self.logging_directory_value = QLabel(str(self._data_directory))
        self.logging_directory_value.setObjectName("loggingDirectory")
        self.logging_directory_value.setToolTip(str(self._data_directory))
        logging_row.addWidget(self.logging_directory_value, 1)
        self.choose_logging_directory_button = QPushButton("Choose...")
        self.choose_logging_directory_button.clicked.connect(self._choose_logging_directory)
        logging_row.addWidget(self.choose_logging_directory_button)
        logging_row.addWidget(QLabel("Interval (s):"))
        self.logging_interval = QDoubleSpinBox()
        self.logging_interval.setObjectName("loggingInterval")
        self.logging_interval.setRange(0.1, 3600.0)
        self.logging_interval.setDecimals(3)
        self.logging_interval.setValue(1.0)
        logging_row.addWidget(self.logging_interval)
        layout.addLayout(logging_row)

        panels = QHBoxLayout()
        panels.setSpacing(18)
        self.channel_controllers: dict[int, ManualChannelController] = {}
        self.logging_controllers: dict[int, ChannelLoggingController] = {}
        self.protocol_controllers: dict[int, ConstantCurrentController] = {}
        self.channel_panels: dict[int, ChannelPanel] = {}
        self.protocol_panels: dict[int, ConstantCurrentPanel] = {}
        for channel in (1, 2):
            controller = ManualChannelController(channel, connection_controller, self)
            logging_controller = ChannelLoggingController(channel, connection_controller, self)
            protocol_controller = ConstantCurrentController(channel, connection_controller, self)
            panel = ChannelPanel(channel)
            protocol_panel = ConstantCurrentPanel(channel)
            panel.refresh_requested.connect(controller.request_refresh)
            panel.voltage_requested.connect(controller.request_set_voltage)
            panel.current_requested.connect(controller.request_set_current)
            panel.output_on_requested.connect(controller.request_output_on)
            panel.output_off_requested.connect(controller.request_output_off)
            controller.snapshot_changed.connect(panel.apply_snapshot)
            controller.snapshot_changed.connect(
                lambda _snapshot, selected_channel=channel:
                self._update_protocol_context(selected_channel)
            )
            logging_controller.snapshot_changed.connect(panel.apply_logging_snapshot)
            logging_controller.snapshot_changed.connect(
                lambda _snapshot, selected_channel=channel:
                self._update_protocol_context(selected_channel)
            )
            logging_controller.snapshot_changed.connect(
                protocol_controller.logging_snapshot_changed
            )
            logging_controller.sample_received.connect(controller.accept_logging_readback)
            protocol_controller.snapshot_changed.connect(protocol_panel.apply_snapshot)
            protocol_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            protocol_controller.sample_received.connect(controller.accept_logging_readback)
            panel.start_logging_requested.connect(
                lambda selected_channel=channel: self._start_logging(selected_channel)
            )
            panel.stop_logging_requested.connect(logging_controller.stop)
            protocol_panel.start_requested.connect(
                lambda current, voltage, duration, interval, selected_channel=channel:
                self._start_protocol(
                    selected_channel, current, voltage, duration, interval
                )
            )
            protocol_panel.pause_requested.connect(protocol_controller.pause)
            protocol_panel.resume_requested.connect(protocol_controller.resume)
            protocol_panel.stop_requested.connect(protocol_controller.stop)
            panel.apply_snapshot(controller.snapshot)
            self.channel_controllers[channel] = controller
            self.logging_controllers[channel] = logging_controller
            self.protocol_controllers[channel] = protocol_controller
            self.channel_panels[channel] = panel
            self.protocol_panels[channel] = protocol_panel
            panels.addWidget(panel, 1)
        layout.addLayout(panels)

        protocol_panels = QHBoxLayout()
        protocol_panels.setSpacing(18)
        for channel in (1, 2):
            protocol_panels.addWidget(self.protocol_panels[channel], 1)
            self._update_protocol_context(channel)
        layout.addLayout(protocol_panels)

        self._disconnect_pending = False
        self._close_pending = False
        self._allow_close = False
        self._coordination_deadline = 0.0

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

    @property
    def data_directory(self) -> Path:
        return self._data_directory

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About QPX600DP Electrolyser",
            f"QPX600DP Electrolyser {__version__}\n\n"
            "Milestone 9 independent Constant Current protocol build.\n\n"
            "The QPX600DP is a programmable DC power supply, not a potentiostat.\n"
            "Simulation is the default. Real Hardware uses only a manually entered COM port. "
            "Independent CSV logging and Simulation Mode Constant Current protocols are "
            "available. Plotting, sweeps and emergency-all-off remain future work.",
        )

    def _choose_logging_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose CSV output directory",
            str(self._data_directory),
        )
        if selected:
            self._data_directory = Path(selected).resolve()
            self.logging_directory_value.setText(str(self._data_directory))
            self.logging_directory_value.setToolTip(str(self._data_directory))

    def _start_logging(self, channel: int) -> None:
        if self.protocol_controllers[channel].snapshot.owns_channel:
            self.statusBar().showMessage("Stop the channel protocol before standalone logging.")
            return
        controller = self.logging_controllers[channel]
        if not controller.start(self._data_directory, self.logging_interval.value()):
            self.statusBar().showMessage(controller.snapshot.message)

    def _start_protocol(
        self,
        channel: int,
        current: str,
        voltage: str,
        duration: str,
        interval: str,
    ) -> None:
        manual = self.channel_controllers[channel].snapshot
        protocol = self.protocol_controllers[channel]
        logger = self.logging_controllers[channel]
        if not protocol.start(
            current,
            voltage,
            duration,
            interval,
            manual.voltage_range_name,
            self._data_directory,
            logging_sink=logger if logger.snapshot.active else None,
        ):
            self.statusBar().showMessage(protocol.snapshot.message)

    def _apply_protocol_snapshot(self, channel: int, snapshot: ProtocolSnapshot) -> None:
        self.channel_panels[channel].set_protocol_owned(snapshot.owns_channel)
        self._update_protocol_context(channel)
        if snapshot.operator_warning:
            QMessageBox.critical(
                self,
                f"Channel {channel} output state unconfirmed",
                snapshot.message,
            )

    def _update_protocol_context(self, channel: int) -> None:
        if channel not in self.protocol_panels:
            return
        connection = self._connection_controller.snapshot
        manual = self.channel_controllers[channel].snapshot
        self.protocol_panels[channel].set_context(
            available=(
                connection.state.value == "connected"
                and connection.simulation_mode
                and manual.connected
                and not manual.pending
                and not manual.faulted
            ),
        )

    def _request_disconnect(self) -> None:
        active = [item for item in self.protocol_controllers.values() if item.snapshot.owns_channel]
        if not active:
            self._connection_controller.request_disconnect()
            return
        self._disconnect_pending = True
        self._coordination_deadline = time.monotonic() + 3.0
        for controller in active:
            controller.stop("Protocol stopped before disconnect")
        QTimer.singleShot(25, self._finish_coordinated_disconnect)

    def _finish_coordinated_disconnect(self) -> None:
        if not self._disconnect_pending:
            return
        if any(item.snapshot.owns_channel for item in self.protocol_controllers.values()):
            if time.monotonic() < self._coordination_deadline:
                QTimer.singleShot(25, self._finish_coordinated_disconnect)
                return
            for controller in self.protocol_controllers.values():
                controller.force_shutdown("Timed out while confirming OFF before disconnect.")
        self._disconnect_pending = False
        self._connection_controller.request_disconnect()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._allow_close:
            for controller in self.logging_controllers.values():
                controller.shutdown()
            super().closeEvent(event)
            return
        active = [item for item in self.protocol_controllers.values() if item.snapshot.owns_channel]
        if active:
            event.ignore()
            if not self._close_pending:
                self._close_pending = True
                self._coordination_deadline = time.monotonic() + 3.0
                for controller in active:
                    controller.shutdown()
                QTimer.singleShot(25, self._finish_coordinated_close)
            return
        for controller in self.logging_controllers.values():
            controller.shutdown()
        super().closeEvent(event)

    def _finish_coordinated_close(self) -> None:
        if any(item.snapshot.owns_channel for item in self.protocol_controllers.values()):
            if time.monotonic() < self._coordination_deadline:
                QTimer.singleShot(25, self._finish_coordinated_close)
                return
            for controller in self.protocol_controllers.values():
                controller.force_shutdown("Timed out while confirming OFF during shutdown.")
        self._close_pending = False
        self._allow_close = True
        self.close()

    def _apply_connection_snapshot(self, snapshot: ConnectionSnapshot) -> None:
        self.connection_panel.apply_snapshot(snapshot)
        for panel in self.channel_panels.values():
            panel.set_real_hardware_mode(not snapshot.simulation_mode)
        for channel in self.protocol_panels:
            self._update_protocol_context(channel)
        self.statusBar().showMessage(snapshot.message)

    def _show_connection_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Connection problem", message)
