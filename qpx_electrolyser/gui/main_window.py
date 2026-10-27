"""Compose guarded simulation/real connection and manual channel controls."""

from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QScrollArea,
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
    REAL_HARDWARE_PROTOCOL_CONFIRMATION,
)
from qpx_electrolyser.engine.constant_voltage import (
    ConstantVoltageController,
    REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
)
from qpx_electrolyser.engine.current_input import CurrentInputMode, CurrentInputSelection
from qpx_electrolyser.engine.stepped_scan import (
    CurrentScanController,
    REAL_HARDWARE_SCAN_CONFIRMATION,
    VoltageScanController,
    generate_inclusive_setpoints,
)
from qpx_electrolyser.gui.channel_panel import ChannelPanel
from qpx_electrolyser.gui.connection_panel import ConnectionPanel
from qpx_electrolyser.gui.constant_current_panel import ConstantCurrentPanel
from qpx_electrolyser.gui.constant_voltage_panel import ConstantVoltagePanel
from qpx_electrolyser.gui.current_scan_panel import CurrentScanPanel
from qpx_electrolyser.gui.plots.realtime import ChannelPlotController, ChannelPlotPanel
from qpx_electrolyser.gui.voltage_scan_panel import VoltageScanPanel


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
        self.setWindowTitle("QPX600DP Electrolyser - Stepped Scans")
        self.resize(1280, 960)
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
        self.voltage_protocol_controllers: dict[int, ConstantVoltageController] = {}
        self.voltage_scan_controllers: dict[int, VoltageScanController] = {}
        self.current_scan_controllers: dict[int, CurrentScanController] = {}
        self.plot_controllers: dict[int, ChannelPlotController] = {}
        self.channel_panels: dict[int, ChannelPanel] = {}
        self.protocol_panels: dict[int, ConstantCurrentPanel] = {}
        self.voltage_protocol_panels: dict[int, ConstantVoltagePanel] = {}
        self.voltage_scan_panels: dict[int, VoltageScanPanel] = {}
        self.current_scan_panels: dict[int, CurrentScanPanel] = {}
        self.plot_panels: dict[int, ChannelPlotPanel] = {}
        for channel in (1, 2):
            controller = ManualChannelController(channel, connection_controller, self)
            logging_controller = ChannelLoggingController(channel, connection_controller, self)
            protocol_controller = ConstantCurrentController(channel, connection_controller, self)
            voltage_protocol_controller = ConstantVoltageController(
                channel, connection_controller, self
            )
            voltage_scan_controller = VoltageScanController(
                channel, connection_controller, self
            )
            current_scan_controller = CurrentScanController(
                channel, connection_controller, self
            )
            plot_controller = ChannelPlotController(channel, connection_controller, self)
            panel = ChannelPanel(channel)
            protocol_panel = ConstantCurrentPanel(channel)
            voltage_protocol_panel = ConstantVoltagePanel(channel)
            voltage_scan_panel = VoltageScanPanel(channel)
            current_scan_panel = CurrentScanPanel(channel)
            plot_panel = ChannelPlotPanel(channel)
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
            logging_controller.snapshot_changed.connect(
                voltage_protocol_controller.logging_snapshot_changed
            )
            logging_controller.snapshot_changed.connect(
                voltage_scan_controller.logging_snapshot_changed
            )
            logging_controller.snapshot_changed.connect(
                current_scan_controller.logging_snapshot_changed
            )
            logging_controller.sample_received.connect(controller.accept_logging_readback)
            logging_controller.sample_received.connect(plot_controller.accept_sample)
            protocol_controller.snapshot_changed.connect(protocol_panel.apply_snapshot)
            protocol_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            protocol_controller.sample_received.connect(controller.accept_logging_readback)
            protocol_controller.sample_received.connect(plot_controller.accept_sample)
            voltage_protocol_controller.snapshot_changed.connect(
                voltage_protocol_panel.apply_snapshot
            )
            voltage_protocol_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            voltage_protocol_controller.sample_received.connect(
                controller.accept_logging_readback
            )
            voltage_protocol_controller.sample_received.connect(plot_controller.accept_sample)
            voltage_scan_controller.snapshot_changed.connect(
                voltage_scan_panel.apply_snapshot
            )
            voltage_scan_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            voltage_scan_controller.sample_received.connect(
                controller.accept_logging_readback
            )
            voltage_scan_controller.sample_received.connect(plot_controller.accept_sample)
            current_scan_controller.snapshot_changed.connect(
                current_scan_panel.apply_snapshot
            )
            current_scan_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            current_scan_controller.sample_received.connect(
                controller.accept_logging_readback
            )
            current_scan_controller.sample_received.connect(plot_controller.accept_sample)
            plot_controller.snapshot_changed.connect(plot_panel.apply_snapshot)
            plot_panel.clear_requested.connect(plot_controller.reset)
            panel.start_logging_requested.connect(
                lambda selected_channel=channel: self._start_logging(selected_channel)
            )
            panel.stop_logging_requested.connect(logging_controller.stop)
            protocol_panel.start_requested.connect(
                lambda current, voltage, duration, interval, mode, area,
                selected_channel=channel:
                self._start_protocol(
                    selected_channel, current, voltage, duration, interval, mode, area
                )
            )
            protocol_panel.pause_requested.connect(protocol_controller.pause)
            protocol_panel.resume_requested.connect(
                lambda selected_channel=channel: self._resume_protocol(selected_channel)
            )
            protocol_panel.stop_requested.connect(protocol_controller.stop)
            voltage_protocol_panel.start_requested.connect(
                lambda voltage, current, duration, interval, selected_channel=channel:
                self._start_voltage_protocol(
                    selected_channel, voltage, current, duration, interval
                )
            )
            voltage_protocol_panel.pause_requested.connect(
                voltage_protocol_controller.pause
            )
            voltage_protocol_panel.resume_requested.connect(
                lambda selected_channel=channel:
                self._resume_voltage_protocol(selected_channel)
            )
            voltage_protocol_panel.stop_requested.connect(
                voltage_protocol_controller.stop
            )
            voltage_scan_panel.start_requested.connect(
                lambda start, stop, step, current, dwell, interval,
                selected_channel=channel: self._start_voltage_scan(
                    selected_channel, start, stop, step, current, dwell, interval
                )
            )
            voltage_scan_panel.pause_requested.connect(voltage_scan_controller.pause)
            voltage_scan_panel.resume_requested.connect(
                lambda selected_channel=channel: self._resume_scan(
                    selected_channel, "voltage"
                )
            )
            voltage_scan_panel.stop_requested.connect(voltage_scan_controller.stop)
            current_scan_panel.start_requested.connect(
                lambda start, stop, step, voltage, dwell, interval, mode, area,
                selected_channel=channel: self._start_current_scan(
                    selected_channel, start, stop, step, voltage, dwell, interval,
                    mode, area
                )
            )
            current_scan_panel.pause_requested.connect(current_scan_controller.pause)
            current_scan_panel.resume_requested.connect(
                lambda selected_channel=channel: self._resume_scan(
                    selected_channel, "current"
                )
            )
            current_scan_panel.stop_requested.connect(current_scan_controller.stop)
            panel.apply_snapshot(controller.snapshot)
            plot_panel.apply_snapshot(plot_controller.snapshot)
            self.channel_controllers[channel] = controller
            self.logging_controllers[channel] = logging_controller
            self.protocol_controllers[channel] = protocol_controller
            self.voltage_protocol_controllers[channel] = voltage_protocol_controller
            self.voltage_scan_controllers[channel] = voltage_scan_controller
            self.current_scan_controllers[channel] = current_scan_controller
            self.plot_controllers[channel] = plot_controller
            self.channel_panels[channel] = panel
            self.protocol_panels[channel] = protocol_panel
            self.voltage_protocol_panels[channel] = voltage_protocol_panel
            self.voltage_scan_panels[channel] = voltage_scan_panel
            self.current_scan_panels[channel] = current_scan_panel
            self.plot_panels[channel] = plot_panel
            panels.addWidget(panel, 1)
        layout.addLayout(panels)

        protocol_panels = QHBoxLayout()
        protocol_panels.setSpacing(18)
        for channel in (1, 2):
            protocol_panels.addWidget(self.protocol_panels[channel], 1)
            self._update_protocol_context(channel)
        layout.addLayout(protocol_panels)

        voltage_protocol_panels = QHBoxLayout()
        voltage_protocol_panels.setSpacing(18)
        for channel in (1, 2):
            voltage_protocol_panels.addWidget(self.voltage_protocol_panels[channel], 1)
            self._update_protocol_context(channel)
        layout.addLayout(voltage_protocol_panels)

        voltage_scan_panels = QHBoxLayout()
        voltage_scan_panels.setSpacing(18)
        for channel in (1, 2):
            voltage_scan_panels.addWidget(self.voltage_scan_panels[channel], 1)
            self._update_protocol_context(channel)
        layout.addLayout(voltage_scan_panels)

        current_scan_panels = QHBoxLayout()
        current_scan_panels.setSpacing(18)
        for channel in (1, 2):
            current_scan_panels.addWidget(self.current_scan_panels[channel], 1)
            self._update_protocol_context(channel)
        layout.addLayout(current_scan_panels)

        plot_panels = QHBoxLayout()
        plot_panels.setSpacing(18)
        for channel in (1, 2):
            plot_panels.addWidget(self.plot_panels[channel], 1)
        layout.addLayout(plot_panels)

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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.content_scroll = scroll
        self.setCentralWidget(scroll)
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
            "Milestone 13 automatic stepped voltage/current scan build.\n\n"
            "The QPX600DP is a programmable DC power supply, not a potentiostat.\n"
            "Simulation is the default. Real Hardware uses only a manually entered COM port. "
            "Independent CSV logging and guarded Constant Current/Constant Voltage "
            "protocols are available. Voltage and Current Scan have guarded Real Hardware "
            "paths, but supervised physical scan acceptance remains pending. Live plotting uses "
            "existing queued samples; emergency-all-off remains future work.",
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
        if self._channel_protocol_owned(channel):
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
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> None:
        if (
            self.voltage_protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_scan_controllers[channel].snapshot.owns_channel
            or self.current_scan_controllers[channel].snapshot.owns_channel
        ):
            self.statusBar().showMessage(
                f"Stop the active CH{channel} protocol first."
            )
            return
        manual = self.channel_controllers[channel].snapshot
        protocol = self.protocol_controllers[channel]
        logger = self.logging_controllers[channel]
        validation_error = protocol.validate_inputs(
            current,
            voltage,
            duration,
            interval,
            manual.voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )
        if validation_error is not None:
            self.protocol_panels[channel].show_validation_error(validation_error)
            self.statusBar().showMessage(validation_error)
            return
        resolved = CurrentInputSelection.parse(
            current_input_mode, active_area_cm2
        ).resolve(current, "Target current")
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            log_target = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else f"new channel {channel} CSV in {self._data_directory}"
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Constant Current - CH{channel}",
                "REAL HARDWARE OUTPUT-ON AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                f"Current input mode: {resolved.input_mode.value}\n"
                f"Commanded current: {float(resolved.current_a):g} A\n"
                + (
                    f"Current density / active area: "
                    f"{float(resolved.current_density_a_cm2):g} A/cm² / "
                    f"{float(resolved.active_area_cm2):g} cm²\n"
                    if resolved.current_density_a_cm2 is not None
                    and resolved.active_area_cm2 is not None else ""
                )
                +
                f"Voltage compliance: {voltage} V\n"
                f"Active duration: {duration} s\n"
                f"Measurement interval: {interval} s\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {log_target}\n\n"
                "Confirm the intended load and wiring are connected to this channel, the other "
                "channel is safe, and energizing this output is authorized. The application will "
                "perform a fresh OFF/settings preflight before one Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware protocol cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_PROTOCOL_CONFIRMATION
        if not protocol.start(
            current,
            voltage,
            duration,
            interval,
            manual.voltage_range_name,
            self._data_directory,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
        ):
            self.statusBar().showMessage(protocol.snapshot.message)

    def _resume_protocol(self, channel: int) -> None:
        protocol = self.protocol_controllers[channel]
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            config = protocol.snapshot.config
            if config is None:
                return
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE resume - CH{channel}",
                "REAL HARDWARE OUTPUT-ON AUTHORIZATION\n\n"
                f"Resume CH{channel} at {config.target_current_a:g} A with "
                f"{config.voltage_compliance_v:g} V compliance?\n\n"
                "Confirm the load and wiring remain safe. A fresh OFF/settings preflight will "
                "precede one Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware resume cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_PROTOCOL_CONFIRMATION
        if not protocol.resume(confirmation):
            self.statusBar().showMessage(protocol.snapshot.message)

    def _start_voltage_protocol(
        self,
        channel: int,
        voltage: str,
        current: str,
        duration: str,
        interval: str,
    ) -> None:
        if (
            self.protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_scan_controllers[channel].snapshot.owns_channel
            or self.current_scan_controllers[channel].snapshot.owns_channel
        ):
            self.statusBar().showMessage(
                f"Stop the active CH{channel} protocol first."
            )
            return
        manual = self.channel_controllers[channel].snapshot
        protocol = self.voltage_protocol_controllers[channel]
        logger = self.logging_controllers[channel]
        validation_error = protocol.validate_inputs(
            voltage, current, duration, interval, manual.voltage_range_name
        )
        if validation_error is not None:
            self.statusBar().showMessage(validation_error)
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            log_target = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else f"new channel {channel} CSV in {self._data_directory}"
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Constant Voltage - CH{channel}",
                "REAL HARDWARE OUTPUT-ON AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                f"Target voltage: {voltage} V\n"
                f"Current limit: {current} A\n"
                f"Active duration: {duration} s\n"
                f"Measurement interval: {interval} s\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {log_target}\n\n"
                "Confirm the intended load and wiring are connected to this channel, the other "
                "channel is safe, and energizing this output is authorized. The application will "
                "perform a fresh OFF/range/settings preflight before one Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware Constant Voltage cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION
        if not protocol.start(
            voltage,
            current,
            duration,
            interval,
            manual.voltage_range_name,
            self._data_directory,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
        ):
            self.statusBar().showMessage(protocol.snapshot.message)

    def _resume_voltage_protocol(self, channel: int) -> None:
        protocol = self.voltage_protocol_controllers[channel]
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            config = protocol.snapshot.config
            if config is None:
                return
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Constant Voltage resume - CH{channel}",
                "REAL HARDWARE OUTPUT-ON AUTHORIZATION\n\n"
                f"Resume CH{channel} at {config.target_voltage_v:g} V with "
                f"{config.current_limit_a:g} A current limit?\n\n"
                "Confirm the load and wiring remain safe. A fresh OFF/range/settings preflight "
                "will precede one Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware Constant Voltage resume cancelled; "
                    "no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION
        if not protocol.resume(confirmation):
            self.statusBar().showMessage(protocol.snapshot.message)

    def _start_voltage_scan(
        self,
        channel: int,
        start_voltage: str,
        stop_voltage: str,
        voltage_step: str,
        current_limit: str,
        dwell: str,
        interval: str,
    ) -> None:
        if (
            self.protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_protocol_controllers[channel].snapshot.owns_channel
            or self.current_scan_controllers[channel].snapshot.owns_channel
        ):
            self.statusBar().showMessage(
                f"Stop the active CH{channel} protocol before starting a Voltage Scan."
            )
            return
        manual = self.channel_controllers[channel].snapshot
        scan = self.voltage_scan_controllers[channel]
        logger = self.logging_controllers[channel]
        validation_error = scan.validate_inputs(
            start_voltage,
            stop_voltage,
            voltage_step,
            current_limit,
            dwell,
            interval,
            manual.voltage_range_name,
        )
        if validation_error is not None:
            self.voltage_scan_panels[channel].show_validation_error(validation_error)
            self.statusBar().showMessage(validation_error)
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            logger_path = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else f"new channel {channel} CSV in {self._data_directory}"
            )
            step_count = len(generate_inclusive_setpoints(
                start_voltage, stop_voltage, voltage_step
            ))
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Voltage Scan - CH{channel}",
                "REAL HARDWARE STEPPED-SCAN OUTPUT-ON AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                "Scan type: Voltage Scan\n"
                f"Start / stop / step: {start_voltage} / {stop_voltage} / "
                f"{voltage_step} V\n"
                f"Current limit: {current_limit} A\n"
                f"Dwell / interval: {dwell} / {interval} s\n"
                f"Total scan steps: {step_count}\n"
                "Output-On writes for this Start: exactly one\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {logger_path}\n\n"
                "Confirm the intended 100-ohm load and wiring are safe. The other channel "
                "will not be modified. A fresh identity/OFF/range/settings preflight will "
                "precede one guarded Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware Voltage Scan cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_SCAN_CONFIRMATION
        if not scan.start(
            start_voltage,
            stop_voltage,
            voltage_step,
            current_limit,
            dwell,
            interval,
            manual.voltage_range_name,
            self._data_directory,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
        ):
            self.voltage_scan_panels[channel].show_validation_error(scan.snapshot.message)
            self.statusBar().showMessage(scan.snapshot.message)

    def _start_current_scan(
        self,
        channel: int,
        start_current: str,
        stop_current: str,
        current_step: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        current_input_mode: str = CurrentInputMode.CURRENT.value,
        active_area_cm2: str = "",
    ) -> None:
        if (
            self.protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_scan_controllers[channel].snapshot.owns_channel
        ):
            self.statusBar().showMessage(
                f"Stop the active CH{channel} protocol before starting a Current Scan."
            )
            return
        manual = self.channel_controllers[channel].snapshot
        scan = self.current_scan_controllers[channel]
        logger = self.logging_controllers[channel]
        validation_error = scan.validate_inputs(
            start_current,
            stop_current,
            current_step,
            voltage_compliance,
            dwell,
            interval,
            manual.voltage_range_name,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
        )
        if validation_error is not None:
            self.current_scan_panels[channel].show_validation_error(validation_error)
            self.statusBar().showMessage(validation_error)
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            logger_path = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else f"new channel {channel} CSV in {self._data_directory}"
            )
            step_count = len(generate_inclusive_setpoints(
                start_current, stop_current, current_step, axis_label="Current"
            ))
            selection = CurrentInputSelection.parse(
                current_input_mode, active_area_cm2
            )
            start_a = selection.resolve(start_current, "Start current").current_a
            stop_a = selection.resolve(stop_current, "Stop current").current_a
            step_a = selection.resolve(current_step, "Current step").current_a
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Current Scan - CH{channel}",
                "REAL HARDWARE STEPPED-SCAN OUTPUT-ON AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                "Scan type: Current Scan\n"
                f"Current input mode: {current_input_mode}\n"
                f"Start / stop / step: {float(start_a):g} / {float(stop_a):g} / "
                f"{float(step_a):g} A\n"
                f"Voltage compliance: {voltage_compliance} V\n"
                f"Dwell / interval: {dwell} / {interval} s\n"
                f"Total scan steps: {step_count}\n"
                "Output-On writes for this Start: exactly one\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {logger_path}\n\n"
                "Confirm the intended 100-ohm load and wiring are safe. The other channel "
                "will not be modified. A fresh identity/OFF/range/settings preflight will "
                "precede one guarded Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware Current Scan cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_SCAN_CONFIRMATION
        if not scan.start(
            start_current,
            stop_current,
            current_step,
            voltage_compliance,
            dwell,
            interval,
            manual.voltage_range_name,
            self._data_directory,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
        ):
            self.current_scan_panels[channel].show_validation_error(scan.snapshot.message)
            self.statusBar().showMessage(scan.snapshot.message)

    def _resume_scan(self, channel: int, scan_type: str) -> None:
        scan = (
            self.voltage_scan_controllers[channel]
            if scan_type == "voltage"
            else self.current_scan_controllers[channel]
        )
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            config = scan.snapshot.config
            if config is None:
                return
            commanded = config.setpoints[
                max(0, (scan.snapshot.scan_step_index or 1) - 1)
            ]
            fixed_label = "Current limit" if scan_type == "voltage" else "Voltage compliance"
            fixed_value = (
                f"{config.fixed_current_a:g} A"
                if scan_type == "voltage"
                else f"{config.fixed_voltage_v:g} V"
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE {scan_type.title()} Scan resume - CH{channel}",
                "REAL HARDWARE STEPPED-SCAN OUTPUT-ON AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                f"Scan type: {scan_type.title()} Scan\n"
                f"Resume step: {scan.snapshot.scan_step_index}/{scan.snapshot.scan_total_steps}\n"
                f"Commanded scan setpoint: {commanded:g} "
                f"{'V' if scan_type == 'voltage' else 'A'}\n"
                f"{fixed_label}: {fixed_value}\n"
                f"Dwell / interval: {config.dwell_s:g} / {config.interval_s:g} s\n"
                "Output-On writes for this Resume: exactly one\n"
                f"CSV path: {scan.snapshot.file_path}\n\n"
                "Confirm the same load and wiring remain safe. A fresh OFF/range/settings "
                "preflight will precede one guarded Output-On attempt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware {scan_type.title()} Scan resume cancelled; "
                    "no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_SCAN_CONFIRMATION
        if not scan.resume(confirmation):
            self.statusBar().showMessage(scan.snapshot.message)

    def _apply_protocol_snapshot(self, channel: int, snapshot: ProtocolSnapshot) -> None:
        self.channel_panels[channel].set_protocol_owned(
            self._channel_protocol_owned(channel)
        )
        self._update_protocol_context(channel)
        if snapshot.operator_warning:
            QMessageBox.critical(
                self,
                f"Channel {channel} output state unconfirmed",
                snapshot.message,
            )

    def _update_protocol_context(self, channel: int) -> None:
        if (
            channel not in self.protocol_panels
            or channel not in self.voltage_protocol_panels
            or channel not in self.voltage_scan_panels
            or channel not in self.current_scan_panels
        ):
            return
        connection = self._connection_controller.snapshot
        manual = self.channel_controllers[channel].snapshot
        common_available = (
            connection.state.value == "connected"
            and manual.connected
            and not manual.pending
            and not manual.faulted
            and manual.output_enabled is False
        )
        self.protocol_panels[channel].set_context(
            available=(
                common_available
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
            )
        )
        self.voltage_protocol_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
            )
        )
        self.voltage_scan_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
            ),
            simulation_mode=connection.simulation_mode,
        )
        self.current_scan_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
            ),
            simulation_mode=connection.simulation_mode,
        )

    def _channel_protocol_owned(self, channel: int) -> bool:
        return (
            self.protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_scan_controllers[channel].snapshot.owns_channel
            or self.current_scan_controllers[channel].snapshot.owns_channel
        )

    def _all_protocol_controllers(self):
        return (
            *self.protocol_controllers.values(),
            *self.voltage_protocol_controllers.values(),
            *self.voltage_scan_controllers.values(),
            *self.current_scan_controllers.values(),
        )

    def _request_disconnect(self) -> None:
        active = [item for item in self._all_protocol_controllers() if item.snapshot.owns_channel]
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
        if any(item.snapshot.owns_channel for item in self._all_protocol_controllers()):
            if time.monotonic() < self._coordination_deadline:
                QTimer.singleShot(25, self._finish_coordinated_disconnect)
                return
            for controller in self._all_protocol_controllers():
                controller.force_shutdown("Timed out while confirming OFF before disconnect.")
        self._disconnect_pending = False
        self._connection_controller.request_disconnect()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._allow_close:
            for controller in self.logging_controllers.values():
                controller.shutdown()
            super().closeEvent(event)
            return
        active = [item for item in self._all_protocol_controllers() if item.snapshot.owns_channel]
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
        if any(item.snapshot.owns_channel for item in self._all_protocol_controllers()):
            if time.monotonic() < self._coordination_deadline:
                QTimer.singleShot(25, self._finish_coordinated_close)
                return
            for controller in self._all_protocol_controllers():
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
