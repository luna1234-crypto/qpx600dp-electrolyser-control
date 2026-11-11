"""Compose guarded simulation/real connection and manual channel controls."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal
import math
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QComboBox,
    QLineEdit,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from qpx_electrolyser import __version__
from qpx_electrolyser.app.channel_controller import ManualChannelController
from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.app.settings import (
    ApplicationSettings,
    PlotDisplayPreferences,
    SettingsError,
    SettingsStore,
    WindowGeometry,
    normalize_window_geometry,
)
from qpx_electrolyser.data.csv_logging import ChannelLoggingController
from qpx_electrolyser.data.sequence_templates import (
    SequenceTemplate, SequenceTemplateError, load_sequence_template,
    save_sequence_template,
)
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
from qpx_electrolyser.engine.custom_sequence import (
    CustomSequenceController,
    REAL_HARDWARE_SEQUENCE_CONFIRMATION,
    SequenceStage,
    SequenceTransitionPolicy,
)
from qpx_electrolyser.engine.polarisation import PolarisationController
from qpx_electrolyser.engine.stepped_scan import (
    CurrentScanController,
    REAL_HARDWARE_SCAN_CONFIRMATION,
    VoltageScanController,
    generate_inclusive_setpoints,
)
from qpx_electrolyser.gui.channel_panel import ChannelPanel
from qpx_electrolyser.gui.channel_workspace import (
    ChannelWorkspace,
    CollapsiblePlotSection,
)
from qpx_electrolyser.gui.connection_panel import ConnectionPanel
from qpx_electrolyser.gui.constant_current_panel import ConstantCurrentPanel
from qpx_electrolyser.gui.constant_voltage_panel import ConstantVoltagePanel
from qpx_electrolyser.gui.current_scan_panel import CurrentScanPanel
from qpx_electrolyser.gui.custom_sequence_panel import CustomSequencePanel
from qpx_electrolyser.gui.input_guard import WheelValueGuard, protect_wheel_value_widgets
from qpx_electrolyser.gui.operator_dashboard import (
    ExperimentSelector,
    LiveDataPage,
    OperatorDashboard,
    card,
    scroll_page,
)
from qpx_electrolyser.gui.plots.realtime import ChannelPlotController, ChannelPlotPanel
from qpx_electrolyser.gui.plots.lsv import LsvPlotController, LsvPlotPanel
from qpx_electrolyser.gui.plots.polarisation import (
    PolarisationPlotController,
    PolarisationPlotPanel,
)
from qpx_electrolyser.gui.polarisation_panel import PolarisationPanel
from qpx_electrolyser.gui.safety_panel import SafetyPanel
from qpx_electrolyser.gui.voltage_scan_panel import VoltageScanPanel
from qpx_electrolyser.safety.emergency import EmergencyAllOffController, EmergencySnapshot
from qpx_electrolyser.safety.event_log import SafetyEventLog
from qpx_electrolyser.safety.model import SafetyLimits, SafetyValidationError, SafetyValidator


class MainWindow(QMainWindow):
    """Compose widgets and route intent; never call the backend directly."""

    def __init__(
        self,
        log_file: Path,
        connection_controller: ConnectionController,
        *,
        data_directory: Path | None = None,
        settings_store: SettingsStore | None = None,
        settings: ApplicationSettings | None = None,
        settings_warnings: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self._connection_controller = connection_controller
        restore_connection_preferences = settings is not None
        restored = settings or replace(
            ApplicationSettings.defaults(data_directory),
            manual_port=connection_controller.snapshot.port_name,
            simulation_mode=connection_controller.snapshot.simulation_mode,
        )
        if data_directory is not None:
            restored = replace(
                restored,
                data_export_directory=str(Path(data_directory).resolve()),
            )
        self._settings_store = settings_store
        self._restored_settings = restored
        self._settings_warnings = settings_warnings
        self._settings_saved = False
        self.safety_validator = SafetyValidator(restored.safety_limits)
        self.safety_event_log = SafetyEventLog(Path(log_file).parent / "safety_events.csv")
        self._logged_safety_events: set[tuple[object, ...]] = set()
        self._live_validation_status_messages: dict[int, set[str]] = {1: set(), 2: set()}
        self.setWindowTitle("QPX600DP Electrolyser Control")
        self.resize(1280, 960)
        self.setMinimumSize(760, 540)
        if restore_connection_preferences:
            connection_controller.set_port_name(restored.manual_port)
            connection_controller.set_simulation_mode(restored.simulation_mode)

        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(12, 8, 12, 8)
        shell_layout.setSpacing(8)
        fixed_header = QWidget()
        fixed_header.setObjectName("alwaysVisibleControlHeader")
        self.always_visible_header = fixed_header
        fixed_header.setProperty("surface", "commandBar")
        fixed_header.setProperty("hardwareMode", "simulation")
        header_layout = QHBoxLayout(fixed_header)
        header_layout.setContentsMargins(12, 6, 12, 6)
        header_layout.setSpacing(12)

        title = QLabel("QPX600DP Lab")
        title_font = title.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setObjectName("applicationTitle")
        header_layout.addWidget(title)

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
        header_layout.addWidget(self.connection_panel, 1)

        self.safety_panel = SafetyPanel(self.safety_validator, show_emergency=False)
        self.safety_panel.limits_changed.connect(self._apply_safety_limits)
        header_layout.addWidget(self.safety_panel.emergency_button)

        self.logging_settings_widget = QWidget()
        self.logging_settings_widget.setObjectName("csvDefaultsPanel")
        logging_row = QHBoxLayout(self.logging_settings_widget)
        logging_row.setContentsMargins(0, 0, 0, 0)
        logging_row.addWidget(QLabel("CSV output directory:"))
        self._data_directory = Path(restored.data_export_directory).resolve()
        self.logging_directory_value = QLabel(str(self._data_directory))
        self.logging_directory_value.setObjectName("loggingDirectory")
        self.logging_directory_value.setToolTip(str(self._data_directory))
        logging_row.addWidget(self.logging_directory_value, 1)
        self.choose_logging_directory_button = QPushButton("Choose...")
        self.choose_logging_directory_button.clicked.connect(self._choose_logging_directory)
        logging_row.addWidget(self.choose_logging_directory_button)
        logging_row.addWidget(QLabel("Default active area (cm²):"))
        self.default_active_area = QDoubleSpinBox()
        self.default_active_area.setObjectName("defaultActiveArea")
        self.default_active_area.setDecimals(6)
        self.default_active_area.setRange(0.000001, 1_000_000.0)
        self.default_active_area.setValue(restored.default_active_area_cm2)
        logging_row.addWidget(self.default_active_area)
        logging_row.addWidget(QLabel("Default logging interval (s):"))
        self.logging_interval = QDoubleSpinBox()
        self.logging_interval.setObjectName("loggingInterval")
        self.logging_interval.setRange(0.1, 3600.0)
        self.logging_interval.setDecimals(3)
        self.logging_interval.setValue(restored.default_sampling_interval_s)
        logging_row.addWidget(self.logging_interval)
        shell_layout.addWidget(fixed_header)

        self.channel_controllers: dict[int, ManualChannelController] = {}
        self.logging_controllers: dict[int, ChannelLoggingController] = {}
        self.protocol_controllers: dict[int, ConstantCurrentController] = {}
        self.voltage_protocol_controllers: dict[int, ConstantVoltageController] = {}
        self.voltage_scan_controllers: dict[int, VoltageScanController] = {}
        self.current_scan_controllers: dict[int, CurrentScanController] = {}
        self.polarisation_controllers: dict[int, PolarisationController] = {}
        self.sequence_controllers: dict[int, CustomSequenceController] = {}
        self.plot_controllers: dict[int, ChannelPlotController] = {}
        self.lsv_plot_controllers: dict[int, LsvPlotController] = {}
        self.polarisation_plot_controllers: dict[int, PolarisationPlotController] = {}
        self.channel_panels: dict[int, ChannelPanel] = {}
        self.protocol_panels: dict[int, ConstantCurrentPanel] = {}
        self.voltage_protocol_panels: dict[int, ConstantVoltagePanel] = {}
        self.voltage_scan_panels: dict[int, VoltageScanPanel] = {}
        self.current_scan_panels: dict[int, CurrentScanPanel] = {}
        self.polarisation_panels: dict[int, PolarisationPanel] = {}
        self.sequence_panels: dict[int, CustomSequencePanel] = {}
        self.plot_panels: dict[int, ChannelPlotPanel] = {}
        self.lsv_plot_panels: dict[int, LsvPlotPanel] = {}
        self.polarisation_plot_panels: dict[int, PolarisationPlotPanel] = {}
        for channel in (1, 2):
            controller = ManualChannelController(
                channel,
                connection_controller,
                self,
                safety_validator=self.safety_validator,
            )
            logging_controller = ChannelLoggingController(channel, connection_controller, self)
            protocol_controller = ConstantCurrentController(
                channel, connection_controller, self,
                safety_validator=self.safety_validator,
            )
            voltage_protocol_controller = ConstantVoltageController(
                channel, connection_controller, self,
                safety_validator=self.safety_validator,
            )
            voltage_scan_controller = VoltageScanController(
                channel, connection_controller, self,
                safety_validator=self.safety_validator,
            )
            current_scan_controller = CurrentScanController(
                channel, connection_controller, self,
                safety_validator=self.safety_validator,
            )
            polarisation_controller = PolarisationController(
                channel, connection_controller, self,
                safety_validator=self.safety_validator,
            )
            sequence_controller = CustomSequenceController(
                channel, connection_controller, self,
                safety_validator=self.safety_validator,
            )
            plot_controller = ChannelPlotController(channel, connection_controller, self)
            lsv_plot_controller = LsvPlotController(channel, self)
            polarisation_plot_controller = PolarisationPlotController(channel, self)
            panel = ChannelPanel(channel)
            protocol_panel = ConstantCurrentPanel(channel)
            voltage_protocol_panel = ConstantVoltagePanel(channel)
            voltage_scan_panel = VoltageScanPanel(channel)
            current_scan_panel = CurrentScanPanel(channel)
            polarisation_panel = PolarisationPanel(channel)
            sequence_panel = CustomSequencePanel(channel)
            for protocol_widget in (
                protocol_panel, voltage_protocol_panel, voltage_scan_panel,
                current_scan_panel, polarisation_panel, sequence_panel,
            ):
                protocol_widget.start_button.setProperty("semanticAction", "primary")
                protocol_widget.pause_button.setProperty("semanticAction", "pause")
                protocol_widget.resume_button.setProperty("semanticAction", "primary")
                protocol_widget.stop_button.setProperty("semanticAction", "stop")
            plot_panel = ChannelPlotPanel(channel)
            plot_panel.set_cp_plot_style(restored.cp_plot_styles.get(channel, "dots"))
            lsv_plot_panel = LsvPlotPanel(channel)
            polarisation_plot_panel = PolarisationPlotPanel(channel)
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
            logging_controller.snapshot_changed.connect(
                polarisation_controller.logging_snapshot_changed
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
            voltage_scan_controller.point_result_received.connect(lsv_plot_controller.accept_point)
            lsv_plot_controller.snapshot_changed.connect(lsv_plot_panel.apply_snapshot)
            lsv_plot_panel.clear_requested.connect(lsv_plot_controller.reset)
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
            polarisation_controller.snapshot_changed.connect(
                polarisation_panel.apply_snapshot
            )
            polarisation_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            polarisation_controller.sample_received.connect(
                controller.accept_logging_readback
            )
            polarisation_controller.sample_received.connect(plot_controller.accept_sample)
            polarisation_controller.point_result_received.connect(
                polarisation_panel.apply_point
            )
            polarisation_controller.point_result_received.connect(
                polarisation_plot_controller.accept_point
            )
            polarisation_plot_controller.snapshot_changed.connect(
                polarisation_plot_panel.apply_snapshot
            )
            polarisation_plot_panel.clear_requested.connect(
                polarisation_plot_controller.reset
            )
            sequence_controller.snapshot_changed.connect(sequence_panel.apply_snapshot)
            sequence_controller.snapshot_changed.connect(
                lambda snapshot, selected_channel=channel:
                self._apply_protocol_snapshot(selected_channel, snapshot)
            )
            sequence_controller.sample_received.connect(controller.accept_logging_readback)
            sequence_controller.sample_received.connect(plot_controller.accept_sample)
            sequence_controller.lsv_point_received.connect(lsv_plot_controller.accept_point)
            sequence_controller.polarisation_point_received.connect(
                polarisation_panel.apply_point
            )
            sequence_controller.polarisation_point_received.connect(
                polarisation_plot_controller.accept_point
            )
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
                lambda start, stop, step, rate, current, settling, measurements,
                interval, area,
                selected_channel=channel: self._start_voltage_scan(
                    selected_channel, start, stop, step, rate, current, settling,
                    measurements, interval, area
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
            polarisation_panel.start_requested.connect(
                lambda area, start, stop, increment, voltage, dwell, interval, window,
                selected_channel=channel: self._start_polarisation(
                    selected_channel, area, start, stop, increment, voltage,
                    dwell, interval, window
                )
            )
            polarisation_panel.pause_requested.connect(polarisation_controller.pause)
            polarisation_panel.resume_requested.connect(
                lambda selected_channel=channel: self._resume_scan(
                    selected_channel, "polarisation"
                )
            )
            polarisation_panel.stop_requested.connect(polarisation_controller.stop)
            sequence_panel.start_requested.connect(
                lambda name, stages, policy, settling, selected_channel=channel:
                self._start_custom_sequence(
                    selected_channel, name, stages, policy, settling
                )
            )
            sequence_panel.pause_requested.connect(sequence_controller.pause)
            sequence_panel.resume_requested.connect(
                lambda selected_channel=channel:
                self._resume_custom_sequence(selected_channel)
            )
            sequence_panel.stop_requested.connect(sequence_controller.stop)
            sequence_panel.save_template_requested.connect(
                lambda save_as, selected_channel=channel:
                self._save_sequence_template(selected_channel, save_as)
            )
            sequence_panel.load_template_requested.connect(
                lambda selected_channel=channel: self._load_sequence_template(selected_channel)
            )
            sequence_panel.clear_template_requested.connect(
                lambda selected_channel=channel: self._clear_sequence_template(selected_channel)
            )
            panel.apply_snapshot(controller.snapshot)
            plot_panel.apply_snapshot(plot_controller.snapshot)
            self.channel_controllers[channel] = controller
            self.logging_controllers[channel] = logging_controller
            self.protocol_controllers[channel] = protocol_controller
            self.voltage_protocol_controllers[channel] = voltage_protocol_controller
            self.voltage_scan_controllers[channel] = voltage_scan_controller
            self.current_scan_controllers[channel] = current_scan_controller
            self.polarisation_controllers[channel] = polarisation_controller
            self.sequence_controllers[channel] = sequence_controller
            self.plot_controllers[channel] = plot_controller
            self.lsv_plot_controllers[channel] = lsv_plot_controller
            self.polarisation_plot_controllers[channel] = polarisation_plot_controller
            self.channel_panels[channel] = panel
            self.protocol_panels[channel] = protocol_panel
            self.voltage_protocol_panels[channel] = voltage_protocol_panel
            self.voltage_scan_panels[channel] = voltage_scan_panel
            self.current_scan_panels[channel] = current_scan_panel
            self.polarisation_panels[channel] = polarisation_panel
            self.sequence_panels[channel] = sequence_panel
            self.plot_panels[channel] = plot_panel
            self.lsv_plot_panels[channel] = lsv_plot_panel
            self.polarisation_plot_panels[channel] = polarisation_plot_panel
            self._apply_channel_defaults(channel)
            self._wire_live_start_validation(channel, (
                protocol_panel,
                voltage_protocol_panel,
                voltage_scan_panel,
                current_scan_panel,
                polarisation_panel,
                sequence_panel,
            ))
        self.channel_workspaces: dict[int, ChannelWorkspace] = {}

        self.emergency_controller = EmergencyAllOffController(
            connection_controller, self.safety_event_log, self
        )
        self.emergency_controller.register(
            {
                channel: (
                    self.protocol_controllers[channel],
                    self.voltage_protocol_controllers[channel],
                    self.voltage_scan_controllers[channel],
                    self.current_scan_controllers[channel],
                    self.polarisation_controllers[channel],
                    self.sequence_controllers[channel],
                )
                for channel in (1, 2)
            },
            self.channel_controllers,
            self.logging_controllers,
        )
        self.safety_panel.emergency_requested.connect(self.emergency_controller.request)
        self.emergency_controller.snapshot_changed.connect(self._apply_emergency_snapshot)

        # Permanent compact channel summaries live directly below the command bar.
        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        for channel in (1, 2):
            workspace = ChannelWorkspace(channel)
            self.channel_workspaces[channel] = workspace
            summary_row.addWidget(workspace, 1)
            self._update_protocol_context(channel)
        shell_layout.addLayout(summary_row)

        self.dashboard = OperatorDashboard()

        dashboard_body = QWidget()
        dashboard_layout = QVBoxLayout(dashboard_body)
        dashboard_layout.setContentsMargins(4, 4, 4, 4)
        self.safety_summary_card = card(
            "Safety active",
            "CH1 and CH2 software limits are loaded and enforced. Detailed limits are "
            "hidden until explicitly opened; hiding them never disables enforcement.",
            "Edit Safety",
            lambda: self._show_safety_editor(),
        )
        self.safety_summary_card.setObjectName("safetyActiveSummary")
        dashboard_layout.addWidget(self.safety_summary_card)
        dashboard_layout.addWidget(card(
            "Ready for supervised operation",
            "Choose Manual Control or Experiments after connecting. Simulation is the "
            "default. Real Hardware never connects automatically and always uses guarded "
            "confirmations. This is two-electrode power-supply control, not potentiostat control.",
        ))
        dashboard_layout.addStretch(1)
        self.content_scroll = scroll_page(dashboard_body, "dashboardScroll")
        self.dashboard.add_page(self.content_scroll)

        manual_body = QWidget()
        manual_layout = QHBoxLayout(manual_body)
        manual_layout.setContentsMargins(4, 4, 4, 4)
        manual_layout.setSpacing(12)
        for channel in (1, 2):
            self.channel_panels[channel].setFlat(True)
            manual_layout.addWidget(self.channel_panels[channel], 1)
        self.manual_scroll = scroll_page(manual_body, "manualControlScroll")
        self.dashboard.add_page(self.manual_scroll)

        self.experiment_selector = ExperimentSelector({
            channel: (
                self.protocol_panels[channel],
                self.voltage_protocol_panels[channel],
                self.voltage_scan_panels[channel],
                self.current_scan_panels[channel],
                self.polarisation_panels[channel],
                self.sequence_panels[channel],
            )
            for channel in (1, 2)
        })
        self.experiment_selector.selection_changed.connect(
            self._sync_legacy_experiment_selection
        )
        self.dashboard.add_page(self.experiment_selector)

        self.live_data_page = LiveDataPage(self.plot_panels)
        self.plot_panels[1].canvas.zoom_changed.connect(
            self.plot_panels[2].canvas.set_horizontal_zoom
        )
        self.plot_panels[2].canvas.zoom_changed.connect(
            self.plot_panels[1].canvas.set_horizontal_zoom
        )
        live_tabs = QTabWidget()
        live_tabs.setObjectName("liveDataPlotTabs")
        live_tabs.setDocumentMode(True)
        live_tabs.addTab(self.live_data_page, "Voltage / Current vs Time")
        lsv_section = CollapsiblePlotSection(
            "Staircase LSV I–V / j–V plots",
            (self.lsv_plot_panels[1], self.lsv_plot_panels[2]),
        )
        polarisation_section = CollapsiblePlotSection(
            "Polarisation plots",
            (self.polarisation_plot_panels[1], self.polarisation_plot_panels[2]),
        )
        live_tabs.addTab(lsv_section, "LSV")
        live_tabs.addTab(polarisation_section, "Polarisation")
        self.dashboard.add_page(live_tabs)
        self.plot_sections = {
            "time": self.live_data_page,
            "lsv": lsv_section,
            "polarisation": polarisation_section,
        }

        files_body = QWidget()
        files_layout = QVBoxLayout(files_body)
        files_layout.setContentsMargins(4, 4, 4, 4)
        files_layout.addWidget(card(
            "Raw CSV files",
            "Every new run asks for an operator-selected raw CSV path. Files are never "
            "silently overwritten; Pause/Resume keeps the same writer.",
        ))
        files_layout.addWidget(self.logging_settings_widget)
        sequence_card = card(
            "Custom Sequence templates",
            "Save, Save As, Load and Clear validated *.qpxseq.txt templates in the "
            "Custom Sequence editor. Loading a template never connects or starts output.",
            "Open Custom Sequence",
            lambda: self._show_experiment(1, 5),
        )
        files_layout.addWidget(sequence_card)
        files_layout.addStretch(1)
        self.dashboard.add_page(scroll_page(files_body, "filesTemplatesScroll"))

        settings_body = QWidget()
        settings_layout = QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(4, 4, 4, 4)
        settings_summary = card(
            "Safety active",
            "Independent CH1/CH2 limits are restored and enforced immediately. "
            "The detailed editor starts hidden.",
            "Edit Safety",
            lambda: self._toggle_safety_editor(True),
        )
        settings_layout.addWidget(settings_summary)
        self.main_vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_vertical_splitter.setObjectName("safetyChannelSplitter")
        self.main_vertical_splitter.setChildrenCollapsible(True)
        safety_placeholder = QWidget()
        placeholder_layout = QVBoxLayout(safety_placeholder)
        placeholder_layout.setContentsMargins(0, 0, 0, 0)
        placeholder_layout.addWidget(self.safety_panel)
        self.main_vertical_splitter.addWidget(safety_placeholder)
        settings_defaults = QWidget()
        defaults_layout = QVBoxLayout(settings_defaults)
        defaults_layout.setContentsMargins(0, 0, 0, 0)
        defaults_layout.addWidget(card(
            "Display and defaults",
            "Theme is available from View → Theme. Connection and protocol state are "
            "never restored; every session starts disconnected.",
        ))
        self.diagnostics_detail = QLabel(
            f"Application diagnostics: {log_file}\n"
            f"Append-only safety events: {self.safety_event_log.path}"
        )
        self.diagnostics_detail.setObjectName("diagnosticsEventHistory")
        self.diagnostics_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.diagnostics_detail.setWordWrap(True)
        self.diagnostics_detail.hide()
        self.show_diagnostics_button = QPushButton("Diagnostics & event history")
        self.show_diagnostics_button.setCheckable(True)
        self.show_diagnostics_button.toggled.connect(self.diagnostics_detail.setVisible)
        defaults_layout.addWidget(self.show_diagnostics_button)
        defaults_layout.addWidget(self.diagnostics_detail)
        self.collapse_safety_button = QPushButton("Hide Safety Editor")
        self.restore_safety_button = QPushButton("Edit Safety")
        self.collapse_safety_button.clicked.connect(self._collapse_safety_panel)
        self.restore_safety_button.clicked.connect(self._restore_safety_panel)
        button_row = QHBoxLayout()
        button_row.addWidget(self.collapse_safety_button)
        button_row.addWidget(self.restore_safety_button)
        button_row.addStretch(1)
        defaults_layout.addLayout(button_row)
        defaults_layout.addStretch(1)
        self.main_vertical_splitter.addWidget(settings_defaults)
        self.main_vertical_splitter.setSizes([0, 1])
        self._last_safety_splitter_sizes = (
            max(180, restored.safety_splitter_sizes[0]),
            max(180, restored.safety_splitter_sizes[1]),
        )
        settings_layout.addWidget(self.main_vertical_splitter, 1)
        self.dashboard.add_page(scroll_page(settings_body, "settingsScroll"))
        shell_layout.addWidget(self.dashboard, 1)
        self.collapse_safety_button.setEnabled(False)

        self._disconnect_pending = False
        self._close_pending = False
        self._allow_close = False
        self._coordination_deadline = 0.0

        self.setCentralWidget(shell)
        self.statusBar().showMessage("Disconnected | Simulation available | Diagnostic logging active")
        self.statusBar().setToolTip(str(log_file))

        file_menu = self.menuBar().addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = self.menuBar().addMenu("&Tools")
        reset_settings_action = QAction("Reset Settings to Defaults...", self)
        reset_settings_action.triggered.connect(self._reset_settings_to_defaults)
        tools_menu.addAction(reset_settings_action)

        view_menu = self.menuBar().addMenu("&View")
        theme_menu = view_menu.addMenu("Theme")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        self.light_theme_action = QAction("Light", self, checkable=True)
        self.dark_theme_action = QAction("Dark", self, checkable=True)
        self.theme_action_group.addAction(self.light_theme_action)
        self.theme_action_group.addAction(self.dark_theme_action)
        theme_menu.addActions(self.theme_action_group.actions())
        self.light_theme_action.triggered.connect(lambda: self._apply_theme("light"))
        self.dark_theme_action.triggered.connect(lambda: self._apply_theme("dark"))
        self.show_time_plots_action = QAction("Show live time plots", self)
        self.show_lsv_plots_action = QAction("Show Staircase LSV plots", self)
        self.show_polarisation_plots_action = QAction("Show polarisation plots", self)
        for action, checked in (
            (self.show_time_plots_action, restored.plot_display.show_time_plots),
            (self.show_lsv_plots_action, restored.plot_display.show_lsv_plots),
            (
                self.show_polarisation_plots_action,
                restored.plot_display.show_polarisation_plots,
            ),
        ):
            action.setCheckable(True)
            action.setChecked(checked)
            view_menu.addAction(action)
        self.show_time_plots_action.toggled.connect(
            lambda visible: self._set_plot_group_visible("time", visible)
        )
        self.show_lsv_plots_action.toggled.connect(
            lambda visible: self._set_plot_group_visible("lsv", visible)
        )
        self.show_polarisation_plots_action.toggled.connect(
            lambda visible: self._set_plot_group_visible("polarisation", visible)
        )
        self._apply_plot_preferences(restored.plot_display)
        self._apply_theme(restored.theme)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        self._restore_window_geometry(restored.window_geometry)
        self._wheel_value_guard = WheelValueGuard(self)
        protect_wheel_value_widgets(self, self._wheel_value_guard)
        if settings_warnings:
            self.statusBar().showMessage("; ".join(settings_warnings))

    @property
    def data_directory(self) -> Path:
        return self._data_directory

    def _apply_channel_defaults(self, channel: int) -> None:
        area = f"{self._restored_settings.default_active_area_cm2:g}"
        self.protocol_panels[channel].active_area_input.setText(area)
        self.voltage_scan_panels[channel].active_area_input.setText(area)
        self.current_scan_panels[channel].active_area_input.setText(area)
        self.polarisation_panels[channel].active_area_input.setText(area)
        sequence = self.sequence_panels[channel]
        for index, key in enumerate(sequence.field_keys):
            if key == "active_area_cm2":
                sequence.field_inputs[index].setText(area)

    def _set_plot_group_visible(self, group: str, visible: bool) -> None:
        section = self.plot_sections.get(group)
        if section is not None:
            section.setVisible(visible)

    def _sync_legacy_experiment_selection(self, channel: int, experiment: int) -> None:
        """Keep compatibility selection state without moving or duplicating editors."""
        workspace = self.channel_workspaces.get(channel)
        if workspace is not None:
            workspace.tabs.setCurrentIndex(experiment + 1)

    def _show_experiment(self, channel: int, experiment: int) -> None:
        self.dashboard.show_page("Experiments")
        self.experiment_selector.select(channel, experiment)

    def _show_safety_editor(self) -> None:
        self.dashboard.show_page("Settings")
        self._restore_safety_panel()

    def _toggle_safety_editor(self, visible: bool) -> None:
        if visible:
            self._restore_safety_panel()
        else:
            self._collapse_safety_panel()

    def _apply_plot_preferences(self, preferences: PlotDisplayPreferences) -> None:
        self.show_time_plots_action.setChecked(preferences.show_time_plots)
        self.show_lsv_plots_action.setChecked(preferences.show_lsv_plots)
        self.show_polarisation_plots_action.setChecked(
            preferences.show_polarisation_plots
        )
        self._set_plot_group_visible("time", preferences.show_time_plots)
        self._set_plot_group_visible("lsv", preferences.show_lsv_plots)
        self._set_plot_group_visible(
            "polarisation", preferences.show_polarisation_plots
        )

    def _collapse_safety_panel(self) -> None:
        sizes = tuple(self.main_vertical_splitter.sizes())
        if sizes[0] > 0:
            self._last_safety_splitter_sizes = sizes
        self.main_vertical_splitter.setSizes([0, max(100, sum(sizes))])
        self.collapse_safety_button.setEnabled(False)
        self.restore_safety_button.setEnabled(True)

    def _restore_safety_panel(self) -> None:
        sizes = self._last_safety_splitter_sizes
        if sizes[0] <= 0:
            sizes = ApplicationSettings.defaults().safety_splitter_sizes
        self.main_vertical_splitter.setSizes([max(140, sizes[0]), max(100, sizes[1])])
        self.collapse_safety_button.setEnabled(True)
        self.restore_safety_button.setEnabled(False)

    def _apply_theme(self, theme: str) -> None:
        dark = theme == "dark"
        self.dark_theme_action.setChecked(dark)
        self.light_theme_action.setChecked(not dark)
        self.setProperty("uiTheme", theme)
        colours = {
            "window": "#17191f" if dark else "#f5f6f8",
            "surface": "#22252d" if dark else "#ffffff",
            "surface2": "#2b2f38" if dark else "#f9fafb",
            "text": "#f3f4f6" if dark else "#242424",
            "muted": "#b5bbc5" if dark else "#5f6368",
            "border": "#454b57" if dark else "#d9dde3",
            "hover": "#323844" if dark else "#eef3f8",
        }
        self.setStyleSheet(f"""
            * {{ font-family: 'Segoe UI'; font-size: 10pt; }}
            QMainWindow, QWidget {{ background: {colours['window']}; color: {colours['text']}; }}
            QWidget#alwaysVisibleControlHeader, QFrame[surface='card'],
            QGroupBox[surface='card'] {{ background: {colours['surface']};
                border: 1px solid {colours['border']}; border-radius: 8px; }}
            QWidget#alwaysVisibleControlHeader {{ border-bottom: 3px solid #0f6cbd; }}
            QWidget#alwaysVisibleControlHeader[hardwareMode='real'] {{ border-bottom-color: #d13438; }}
            QLabel#applicationTitle {{ font-size: 14pt; font-weight: 700; }}
            QLabel[role='sectionTitle'] {{ font-size: 11pt; font-weight: 700; }}
            QListWidget#primaryNavigation {{ background: {colours['surface']};
                border: 1px solid {colours['border']}; border-radius: 8px; padding: 4px; }}
            QListWidget#primaryNavigation::item {{ padding: 10px 12px; border-radius: 5px; }}
            QListWidget#primaryNavigation::item:selected {{ background: #0f6cbd; color: white; }}
            QListWidget#primaryNavigation::item:hover:!selected {{ background: {colours['hover']}; }}
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget,
            QTableWidget {{ background: {colours['surface']}; color: {colours['text']};
                border: 1px solid {colours['border']}; border-radius: 4px; padding: 4px; }}
            QPushButton {{ background: {colours['surface2']}; color: {colours['text']};
                border: 1px solid {colours['border']}; padding: 6px 10px; border-radius: 4px; }}
            QPushButton:hover {{ background: {colours['hover']}; }}
            QPushButton:default {{ background: #0f6cbd; color: white; border-color: #0f6cbd; }}
            QPushButton[semanticAction='primary']:enabled {{ background: #0f6cbd; color: white;
                border-color: #0f6cbd; }}
            QPushButton[semanticAction='pause']:enabled {{ background: #f9e08e; color: #4d3b00;
                border-color: #c19c00; }}
            QPushButton[semanticAction='stop']:enabled {{ background: #ca5010; color: white;
                border-color: #a23b00; }}
            QPushButton:disabled {{ color: {colours['muted']}; background: {colours['surface2']}; }}
            QTabWidget::pane {{ border: 1px solid {colours['border']}; background: {colours['surface']}; }}
            QTabBar::tab {{ background: {colours['surface2']}; padding: 7px 12px; }}
            QTabBar::tab:selected {{ background: #0f6cbd; color: white; }}
            QGroupBox {{ border: 0; margin-top: 12px; padding-top: 6px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 4px; }}
            QScrollArea {{ border: 0; background: transparent; }}
            QMenuBar, QMenu, QStatusBar {{ background: {colours['surface']}; color: {colours['text']}; }}
            QToolTip {{ color: {colours['text']}; background: {colours['surface']};
                border: 1px solid {colours['border']}; padding: 4px; }}
            QSplitter::handle {{ background: {colours['border']}; height: 4px; width: 4px; }}
        """)
        self.connection_panel.simulation_checkbox.update()
        self.connection_panel.real_hardware_radio.update()
        for panel in self.plot_panels.values():
            panel.canvas.update()

    def _save_sequence_template(self, channel: int, save_as: bool) -> None:
        panel = self.sequence_panels[channel]
        manual = self.channel_controllers[channel].snapshot
        try:
            self.sequence_controllers[channel].validate_template(
                panel.name_input.text(), panel.stages,
                manual.voltage_range_name or "80 V",
            )
        except ValueError as error:
            panel.show_validation_error(f"Template validation failed: {error}")
            return
        selected = panel.template_path
        if save_as or not selected:
            selected, _ = QFileDialog.getSaveFileName(
                self, "Save Custom Sequence template",
                str(self._data_directory / f"{panel.name_input.text().strip() or 'sequence'}.qpxseq.txt"),
                "QPX sequence templates (*.qpxseq.txt)",
            )
        if not selected:
            return
        template = SequenceTemplate(
            panel.name_input.text().strip(), panel.notes_input.toPlainText(),
            str(panel.transition_policy.currentData()),
            panel.handoff_settling_input.text(), panel.stages,
        )
        try:
            path = Path(selected)
            save_sequence_template(path, template)
            if not path.name.lower().endswith(".qpxseq.txt"):
                path = path.with_name(path.name + ".qpxseq.txt")
            panel.template_path = str(path)
            self.statusBar().showMessage(f"CH{channel} template saved: {path}")
        except SequenceTemplateError as error:
            panel.show_validation_error(str(error))

    def _load_sequence_template(self, channel: int) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Load Custom Sequence template", str(self._data_directory),
            "QPX sequence templates (*.qpxseq.txt)",
        )
        if not selected:
            return
        panel = self.sequence_panels[channel]
        manual = self.channel_controllers[channel].snapshot
        try:
            template = load_sequence_template(Path(selected))
            SequenceTransitionPolicy(template.transition_policy)
            settling = float(template.handoff_settling_s)
            if not math.isfinite(settling) or not 0 <= settling <= 60:
                raise ValueError("invalid handoff settling time")
            self.sequence_controllers[channel].validate_template(
                template.name, template.stages,
                manual.voltage_range_name or "80 V",
            )
        except (SequenceTemplateError, ValueError) as error:
            panel.show_validation_error(f"Template import rejected: {error}")
            return
        stage_preview = "\n".join(
            f"{index}. {stage.stage_type.label}: "
            + ", ".join(f"{key}={value}" for key, value in stage.parameters)
            for index, stage in enumerate(template.stages, 1)
        )
        answer = QMessageBox.question(
            self, f"Import CH{channel} sequence template?",
            f"Name: {template.name}\nNotes: {template.notes or '—'}\n"
            f"Transition: {template.transition_policy}\n\n{stage_preview}\n\n"
            "Importing edits the offline sequence only. It cannot connect, start or enable an output.",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Open:
            return
        panel.set_template(
            name=template.name, notes=template.notes, stages=template.stages,
            transition_policy=template.transition_policy,
            handoff_settling_s=template.handoff_settling_s, path=selected,
        )
        self._update_protocol_context(channel)

    def _clear_sequence_template(self, channel: int) -> None:
        panel = self.sequence_panels[channel]
        if panel._snapshot.owns_channel:
            return
        answer = QMessageBox.question(
            self, f"Clear CH{channel} sequence?", "Remove all stages from the editor?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            panel.clear_template()
            self._update_protocol_context(channel)

    def _restore_window_geometry(self, geometry: WindowGeometry) -> None:
        application = QApplication.instance()
        screens = ()
        if application is not None:
            screens = tuple(
                (
                    screen.availableGeometry().x(),
                    screen.availableGeometry().y(),
                    screen.availableGeometry().width(),
                    screen.availableGeometry().height(),
                )
                for screen in application.screens()
            )
        safe = normalize_window_geometry(geometry, screens)
        self.setGeometry(safe.x, safe.y, safe.width, safe.height)

    def _current_settings(self) -> ApplicationSettings:
        snapshot = self._connection_controller.snapshot
        splitter_sizes = tuple(self.main_vertical_splitter.sizes())
        if splitter_sizes[0] <= 0:
            splitter_sizes = self._last_safety_splitter_sizes
        return ApplicationSettings(
            manual_port=snapshot.port_name,
            data_export_directory=str(self._data_directory),
            safety_limits={
                channel: self.safety_validator.user_limits(channel)
                for channel in (1, 2)
            },
            default_active_area_cm2=self.default_active_area.value(),
            default_sampling_interval_s=self.logging_interval.value(),
            simulation_mode=snapshot.simulation_mode,
            window_geometry=WindowGeometry(
                self.x(), self.y(), self.width(), self.height()
            ),
            plot_display=PlotDisplayPreferences(
                show_time_plots=self.show_time_plots_action.isChecked(),
                show_lsv_plots=self.show_lsv_plots_action.isChecked(),
                show_polarisation_plots=(
                    self.show_polarisation_plots_action.isChecked()
                ),
            ),
            theme=("dark" if self.dark_theme_action.isChecked() else "light"),
            safety_splitter_sizes=splitter_sizes,
            cp_plot_styles={
                channel: str(self.plot_panels[channel].plot_style_combo.currentData())
                for channel in (1, 2)
            },
        )

    def _save_persistent_settings(self) -> None:
        if self._settings_store is None or self._settings_saved:
            return
        try:
            self._settings_store.save(self._current_settings())
            self._settings_saved = True
        except SettingsError as error:
            self.statusBar().showMessage(str(error))

    def _reset_settings_to_defaults(self) -> None:
        if self._settings_store is None:
            self.statusBar().showMessage("Settings persistence is disabled for this session.")
            return
        if (
            self._connection_controller.snapshot.state
            not in {ConnectionState.DISCONNECTED, ConnectionState.ERROR}
            or any(item.snapshot.owns_channel for item in self._all_protocol_controllers())
        ):
            QMessageBox.warning(
                self,
                "Disconnect before resetting settings",
                "Reset is available only while disconnected and with no active protocol.",
            )
            return
        response = QMessageBox.warning(
            self,
            "Reset Settings to Defaults?",
            "This resets the remembered port, mode, directories, safety limits, "
            "defaults, CP plot styles, window geometry and main plot visibility. Existing raw/readable CSV "
            "files and external scientific-analysis files are not modified.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response != QMessageBox.StandardButton.Reset:
            return
        try:
            defaults = self._settings_store.reset(
                default_data_directory=default_paths().data_directory
            )
        except SettingsError as error:
            QMessageBox.warning(self, "Settings reset failed", str(error))
            return
        self._restored_settings = defaults
        self._settings_saved = False
        self._connection_controller.set_port_name("")
        self._connection_controller.set_simulation_mode(True)
        self._data_directory = Path(defaults.data_export_directory)
        self.logging_directory_value.setText(str(self._data_directory))
        self.logging_directory_value.setToolTip(str(self._data_directory))
        self.default_active_area.setValue(defaults.default_active_area_cm2)
        self.logging_interval.setValue(defaults.default_sampling_interval_s)
        for channel in (1, 2):
            limits = defaults.safety_limits[channel]
            self.safety_validator.set_user_limits(channel, limits)
            self.safety_panel.apply_limits(channel, limits)
            self._apply_channel_defaults(channel)
            self.plot_panels[channel].set_cp_plot_style(
                defaults.cp_plot_styles[channel]
            )
            self._update_protocol_context(channel)
        self._apply_plot_preferences(defaults.plot_display)
        self._last_safety_splitter_sizes = defaults.safety_splitter_sizes
        self._collapse_safety_panel()
        self._apply_theme(defaults.theme)
        self._restore_window_geometry(defaults.window_geometry)
        self.statusBar().showMessage(
            f"Settings reset to documented defaults: {self._settings_store.path}"
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About QPX600DP Electrolyser",
            f"QPX600DP Electrolyser {__version__}\n\n"
            "Milestone 18 refined operator interface build.\n\n"
            "The QPX600DP is a programmable DC power supply, not a potentiostat.\n"
            "Simulation is the default. Real Hardware uses only a manually entered COM port. "
            "Independent logging, guarded protocols and scans retain the shared command queue. "
            "Session-local CH1/CH2 safety limits and Emergency All-Off add validation and "
            "authoritative per-channel OFF reporting. This application stores complete raw "
            "experimental CSV data and live plots; scientific post-processing is external.\n\n"
            f"Settings file:\n{self._settings_store.path if self._settings_store else 'Disabled for this session'}",
        )

    def _apply_safety_limits(self, channel: int, limits: SafetyLimits) -> None:
        manual = self.channel_controllers[channel].snapshot
        if self._channel_protocol_owned(channel) or manual.output_enabled or manual.pending:
            self.safety_panel.show_message(
                f"CH{channel} must be idle, command-free and Output OFF before changing "
                "its safety limits.",
                error=True,
            )
            return
        try:
            self.safety_validator.set_user_limits(channel, limits)
        except (TypeError, ValueError, SafetyValidationError) as error:
            self.safety_panel.show_message(str(error), error=True)
            return
        self.safety_panel.show_message(self.safety_validator.summary(channel))
        self._update_protocol_context(channel)

    def _wire_live_start_validation(
        self, channel: int, panels: tuple[QWidget, ...]
    ) -> None:
        """Revalidate UI-only inputs without acquiring ownership or logging."""
        for panel in panels:
            for field in panel.findChildren(QLineEdit):
                field.textChanged.connect(
                    lambda _text, selected=channel: self._refresh_start_validation(selected)
                )
            for choice in panel.findChildren(QComboBox):
                choice.currentIndexChanged.connect(
                    lambda _index, selected=channel: self._refresh_start_validation(selected)
                )
        sequence = self.sequence_panels[channel]
        for button in (
            sequence.add_button,
            sequence.update_button,
            sequence.remove_button,
            sequence.duplicate_button,
            sequence.up_button,
            sequence.down_button,
        ):
            button.clicked.connect(
                lambda _checked=False, selected=channel:
                self._refresh_start_validation(selected)
            )

    def _refresh_start_validation(self, channel: int) -> None:
        """Publish channel-local preflight feedback; never submit queue work."""
        if channel not in self.channel_controllers:
            return
        connection = self._connection_controller.snapshot
        manual = self.channel_controllers[channel].snapshot
        panels = (
            self.protocol_panels[channel],
            self.voltage_protocol_panels[channel],
            self.voltage_scan_panels[channel],
            self.current_scan_panels[channel],
            self.polarisation_panels[channel],
            self.sequence_panels[channel],
        )
        if (
            connection.state.value != "connected"
            or not manual.connected
            or manual.voltage_range_name is None
            or manual.output_enabled is not False
        ):
            for panel in panels:
                panel.set_start_validation(None)
            return

        cc_panel = self.protocol_panels[channel]
        cc_error = self.protocol_controllers[channel].validate_inputs(
            cc_panel.current_input.text(),
            cc_panel.voltage_input.text(),
            cc_panel.duration_input.text(),
            cc_panel.interval_input.text(),
            manual.voltage_range_name,
            current_input_mode=str(cc_panel.input_mode.currentData()),
            active_area_cm2=cc_panel.active_area_input.text(),
        )
        cc_panel.set_start_validation(cc_error)

        cv_panel = self.voltage_protocol_panels[channel]
        cv_error = self.voltage_protocol_controllers[channel].validate_inputs(
            cv_panel.target_voltage_input.text(),
            cv_panel.current_limit_input.text(),
            cv_panel.duration_input.text(),
            cv_panel.interval_input.text(),
            manual.voltage_range_name,
        )
        cv_panel.set_start_validation(cv_error)

        voltage_panel = self.voltage_scan_panels[channel]
        voltage_error = self.voltage_scan_controllers[channel].validate_inputs(
            voltage_panel.start_voltage_input.text(),
            voltage_panel.stop_voltage_input.text(),
            voltage_panel.step_input.text(),
            voltage_panel.scan_rate_input.text(),
            voltage_panel.current_limit_input.text(),
            voltage_panel.settling_input.text(),
            voltage_panel.measurements_input.text(),
            voltage_panel.interval_input.text(),
            manual.voltage_range_name,
            voltage_panel.active_area_input.text(),
        )
        voltage_panel.set_start_validation(voltage_error)

        current_panel = self.current_scan_panels[channel]
        current_error = self.current_scan_controllers[channel].validate_inputs(
            current_panel.start_current_input.text(),
            current_panel.stop_current_input.text(),
            current_panel.step_input.text(),
            current_panel.voltage_compliance_input.text(),
            current_panel.dwell_input.text(),
            current_panel.interval_input.text(),
            manual.voltage_range_name,
            current_input_mode=str(current_panel.input_mode.currentData()),
            active_area_cm2=current_panel.active_area_input.text(),
        )
        current_panel.set_start_validation(current_error)

        polar_panel = self.polarisation_panels[channel]
        polar_error = self.polarisation_controllers[channel].validate_inputs(
            polar_panel.active_area_input.text(),
            polar_panel.start_density_input.text(),
            polar_panel.stop_density_input.text(),
            polar_panel.increment_input.text(),
            polar_panel.voltage_input.text(),
            polar_panel.dwell_input.text(),
            polar_panel.interval_input.text(),
            polar_panel.averaging_input.text(),
            manual.voltage_range_name,
        )
        polar_panel.set_start_validation(polar_error)

        sequence_panel = self.sequence_panels[channel]
        try:
            settling = float(sequence_panel.handoff_settling_input.text())
            if not math.isfinite(settling) or not 0 <= settling <= 60:
                raise ValueError(
                    "Handoff settling time must be finite and between 0 and 60 seconds."
                )
            SequenceTransitionPolicy(str(sequence_panel.transition_policy.currentData()))
            self.sequence_controllers[channel].validate_sequence(
                sequence_panel.name_input.text(),
                sequence_panel.stages,
                manual.voltage_range_name,
            )
            sequence_error = None
        except (TypeError, ValueError) as error:
            sequence_error = str(error)
        sequence_panel.set_start_validation(sequence_error)
        errors = tuple(
            error for error in (
                cc_error, cv_error, voltage_error, current_error,
                polar_error,
                sequence_error if sequence_panel.stages else None,
            ) if error
        )
        previous_statuses = self._live_validation_status_messages[channel]
        self._live_validation_status_messages[channel] = set(errors)
        if errors:
            self.statusBar().showMessage(errors[0])
        elif self.statusBar().currentMessage() in previous_statuses:
            self.statusBar().showMessage(connection.message)

    def _blocked_start(
        self, channel: int, panel: object, protocol_name: str, message: str
    ) -> None:
        panel.show_validation_error(message)
        self.statusBar().showMessage(message)
        self.safety_event_log.append(
            channel=channel,
            cause=message,
            requested_action=f"Blocked unsafe {protocol_name} Start before mutation",
            confirmed_off=(
                self.channel_controllers[channel].snapshot.output_enabled is False
            ),
        )

    def _apply_emergency_snapshot(self, snapshot: EmergencySnapshot) -> None:
        self.safety_panel.set_emergency_pending(snapshot.active)
        self.safety_panel.show_message(snapshot.message, error=snapshot.warning)
        self.statusBar().showMessage(snapshot.message)
        if snapshot.warning:
            QMessageBox.critical(
                self,
                "Emergency All-Off — physical OFF unconfirmed",
                snapshot.message
                + "\n\nUse the QPX600DP front-panel controls immediately and verify both outputs OFF.",
            )

    def _safety_confirmation_details(
        self, channel: int, *, file_path: Path | None = None
    ) -> str:
        manual = self.channel_controllers[channel].snapshot
        logger = self.logging_controllers[channel].snapshot
        csv_target = file_path or logger.file_path
        if csv_target is None:
            csv_description = f"new exclusive CH{channel} CSV in {self._data_directory}"
        else:
            csv_description = str(csv_target)
        return (
            "\n"
            f"Voltage range: {manual.voltage_range_name or 'unavailable'}\n"
            f"CSV path: {csv_description}\n"
            + self.safety_validator.summary(channel)
            + "\nThe request has passed capability/grid, project and user-limit validation."
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

    def _choose_run_csv(
        self, channel: int, run_name: str, *, reuse_active_logger: bool = False
    ) -> Path | None:
        """Choose one exclusive raw CSV before any protocol mutation."""
        if reuse_active_logger and self.logging_controllers[channel].snapshot.active:
            return self._data_directory
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        safe_name = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in run_name.lower()
        ).strip("_") or "run"
        suggested = self._data_directory / f"qpx600dp_ch{channel}_{safe_name}_{stamp}.csv"
        # Headless regression/smoke runs cannot display a native dialog. They still
        # exercise exclusive explicit-path creation; GUI tests can monkeypatch the dialog.
        if (
            QApplication.platformName().lower() == "offscreen"
            and not getattr(self, "_force_native_csv_dialog", False)
        ):
            selected = str(suggested)
        else:
            selected, _ = QFileDialog.getSaveFileName(
                self, f"Save CH{channel} {run_name} CSV", str(suggested),
                "CSV files (*.csv)",
            )
        if not selected:
            self.statusBar().showMessage(
                f"CH{channel} {run_name} cancelled before any instrument command."
            )
            return None
        selected_path = Path(selected)
        selected_name = selected_path.name
        lower_name = selected_name.lower()
        if lower_name.endswith("_raw.csv"):
            base_name = selected_name[:-len("_raw.csv")]
        elif lower_name.endswith("_readable.csv"):
            base_name = selected_name[:-len("_readable.csv")]
        elif selected_path.suffix.lower() == ".csv":
            base_name = selected_path.stem
        else:
            base_name = selected_name
        base_name = base_name.strip()
        if not base_name:
            QMessageBox.warning(self, "CSV destination rejected", "CSV base filename is empty.")
            return None
        path = selected_path.with_name(f"{base_name}_raw.csv")
        readable_path = selected_path.with_name(f"{base_name}_readable.csv")
        try:
            parent = path.parent.resolve()
            if not parent.is_dir() or not os.access(parent, os.W_OK):
                raise OSError(f"Directory is not writable: {parent}")
            path = parent / path.name
            readable_path = parent / readable_path.name
            existing = [candidate for candidate in (path, readable_path) if candidate.exists()]
            if existing:
                raise FileExistsError(
                    "CSV pair already exists and will not be overwritten: "
                    + ", ".join(str(candidate) for candidate in existing)
                )
        except OSError as error:
            QMessageBox.warning(self, "CSV destination rejected", str(error))
            return None
        self._data_directory = path.parent
        self.logging_directory_value.setText(str(self._data_directory))
        self.logging_directory_value.setToolTip(str(self._data_directory))
        return path

    def _start_logging(self, channel: int) -> None:
        if self._channel_protocol_owned(channel):
            self.statusBar().showMessage("Stop the channel protocol before standalone logging.")
            return
        controller = self.logging_controllers[channel]
        destination = self._choose_run_csv(channel, "manual_logging")
        if destination is None:
            return
        if not controller.start(destination, self.logging_interval.value()):
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
            or self.polarisation_controllers[channel].snapshot.owns_channel
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
            self._blocked_start(
                channel, self.protocol_panels[channel],
                "Constant Current", validation_error,
            )
            return
        run_destination = self._choose_run_csv(
            channel, "chronopotentiometry", reuse_active_logger=True
        )
        if run_destination is None:
            return
        resolved = CurrentInputSelection.parse(
            current_input_mode, active_area_cm2
        ).resolve(current, "Target current")
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            log_target = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else str(run_destination)
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Chronopotentiometry (CP) - CH{channel}",
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
                f"Voltage limit / compliance: {voltage} V\n"
                f"Active duration: {duration} s\n"
                f"Measurement interval: {interval} s\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {log_target}\n\n"
                "Confirm the intended load and wiring are connected to this channel, the other "
                "channel is safe, and energizing this output is authorized. The application will "
                "perform a fresh OFF/settings preflight before one Output-On attempt."
                + self._safety_confirmation_details(channel),
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
            run_destination,
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
                + (
                    f"Current density / active area: "
                    f"{config.commanded_current_density_a_cm2:g} A/cm² / "
                    f"{config.active_area_cm2:g} cm²\n"
                    if config.commanded_current_density_a_cm2 is not None
                    and config.active_area_cm2 is not None else ""
                )
                + f"Remaining active duration: {protocol.snapshot.remaining_active_s:g} s\n"
                "Confirm the load and wiring remain safe. A fresh OFF/settings preflight will "
                "precede one Output-On attempt."
                + self._safety_confirmation_details(
                    channel, file_path=protocol.snapshot.file_path
                ),
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
            or self.polarisation_controllers[channel].snapshot.owns_channel
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
            self._blocked_start(
                channel, self.voltage_protocol_panels[channel],
                "Constant Voltage", validation_error,
            )
            return
        run_destination = self._choose_run_csv(
            channel, "chronoamperometry", reuse_active_logger=True
        )
        if run_destination is None:
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            log_target = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else str(run_destination)
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Chronoamperometry (CA) - CH{channel}",
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
                "perform a fresh OFF/range/settings preflight before one Output-On attempt."
                + self._safety_confirmation_details(channel),
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
            run_destination,
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
                f"Confirm REAL HARDWARE Chronoamperometry (CA) resume - CH{channel}",
                "REAL HARDWARE OUTPUT-ON AUTHORIZATION\n\n"
                f"Resume CH{channel} at {config.target_voltage_v:g} V with "
                f"{config.current_limit_a:g} A current limit?\n\n"
                f"Remaining active duration: {protocol.snapshot.remaining_active_s:g} s\n"
                "Confirm the load and wiring remain safe. A fresh OFF/range/settings preflight "
                "will precede one Output-On attempt."
                + self._safety_confirmation_details(
                    channel, file_path=protocol.snapshot.file_path
                ),
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
        voltage_step_mv: str,
        scan_rate_mv_per_s: str,
        current_limit: str,
        settling_time: str,
        measurements_per_point: str,
        interval: str,
        active_area_cm2: str,
    ) -> None:
        if (
            self.protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_protocol_controllers[channel].snapshot.owns_channel
            or self.current_scan_controllers[channel].snapshot.owns_channel
            or self.polarisation_controllers[channel].snapshot.owns_channel
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
            voltage_step_mv,
            scan_rate_mv_per_s,
            current_limit,
            settling_time,
            measurements_per_point,
            interval,
            manual.voltage_range_name,
            active_area_cm2,
        )
        if validation_error is not None:
            self._blocked_start(
                channel, self.voltage_scan_panels[channel],
                "Voltage Sweep / Staircase LSV", validation_error,
            )
            return
        run_destination = self._choose_run_csv(
            channel, "staircase_lsv", reuse_active_logger=True
        )
        if run_destination is None:
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            logger_path = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else str(run_destination)
            )
            step_count = len(generate_inclusive_setpoints(
                start_voltage, stop_voltage, str(Decimal(voltage_step_mv) / Decimal("1000"))
            ))
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Voltage Scan - CH{channel}",
                "REAL HARDWARE STEPPED-SCAN OUTPUT-ON AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                "Scan type: Voltage Sweep / Staircase LSV\n"
                f"Start / end / step: {start_voltage} / {stop_voltage} / "
                f"{voltage_step_mv} mV\n"
                f"Scan rate: {scan_rate_mv_per_s} mV/s\n"
                f"Current limit: {current_limit} A\n"
                f"Settling / measurements / interval: {settling_time} / "
                f"{measurements_per_point} / {interval} s\n"
                f"Total scan steps: {step_count}\n"
                "Output-On writes for this Start: exactly one\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {logger_path}\n\n"
                "Confirm the intended 100-ohm load and wiring are safe. The other channel "
                "will not be modified. A fresh identity/OFF/range/settings preflight will "
                "precede one guarded Output-On attempt."
                + self._safety_confirmation_details(channel),
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
            voltage_step_mv,
            scan_rate_mv_per_s,
            current_limit,
            settling_time,
            measurements_per_point,
            interval,
            manual.voltage_range_name,
            run_destination,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
            active_area_cm2=active_area_cm2,
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
            or self.polarisation_controllers[channel].snapshot.owns_channel
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
            self._blocked_start(
                channel, self.current_scan_panels[channel],
                "Current Scan", validation_error,
            )
            return
        run_destination = self._choose_run_csv(
            channel, "current_scan", reuse_active_logger=True
        )
        if run_destination is None:
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            logger_path = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else str(run_destination)
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
                + (
                    f"Current density start / stop / step: {start_current} / "
                    f"{stop_current} / {current_step} A/cm²\n"
                    f"Active area: {active_area_cm2} cm²\n"
                    if current_input_mode == CurrentInputMode.CURRENT_DENSITY.value else ""
                )
                +
                f"Start / stop / step: {float(start_a):g} / {float(stop_a):g} / "
                f"{float(step_a):g} A\n"
                f"Voltage limit / compliance: {voltage_compliance} V\n"
                f"Dwell / interval: {dwell} / {interval} s\n"
                f"Total scan steps: {step_count}\n"
                "Output-On writes for this Start: exactly one\n"
                f"Current displayed output state: "
                f"{'OFF' if manual.output_enabled is False else 'NOT CONFIRMED OFF'}\n"
                f"CSV destination: {logger_path}\n\n"
                "Confirm the intended 100-ohm load and wiring are safe. The other channel "
                "will not be modified. A fresh identity/OFF/range/settings preflight will "
                "precede one guarded Output-On attempt."
                + self._safety_confirmation_details(channel),
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
            run_destination,
            current_input_mode=current_input_mode,
            active_area_cm2=active_area_cm2,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
        ):
            self.current_scan_panels[channel].show_validation_error(scan.snapshot.message)
            self.statusBar().showMessage(scan.snapshot.message)

    def _resume_scan(self, channel: int, scan_type: str) -> None:
        scan = {
            "voltage": self.voltage_scan_controllers[channel],
            "current": self.current_scan_controllers[channel],
            "polarisation": self.polarisation_controllers[channel],
        }[scan_type]
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            config = scan.snapshot.config
            if config is None:
                return
            commanded = config.setpoints[
                max(0, (scan.snapshot.scan_step_index or 1) - 1)
            ]
            fixed_label = "Current limit" if scan_type == "voltage" else "Voltage limit / compliance"
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
                "preflight will precede one guarded Output-On attempt."
                + self._safety_confirmation_details(
                    channel, file_path=scan.snapshot.file_path
                ),
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

    def _start_polarisation(
        self,
        channel: int,
        active_area: str,
        start_density: str,
        stop_density: str,
        density_increment: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        averaging_window: str,
    ) -> None:
        other_controllers = (
            self.protocol_controllers[channel],
            self.voltage_protocol_controllers[channel],
            self.voltage_scan_controllers[channel],
            self.current_scan_controllers[channel],
        )
        if any(item.snapshot.owns_channel for item in other_controllers):
            message = (
                f"Stop the active CH{channel} protocol before starting polarisation."
            )
            self.polarisation_panels[channel].show_validation_error(message)
            self.statusBar().showMessage(message)
            return
        manual = self.channel_controllers[channel].snapshot
        controller = self.polarisation_controllers[channel]
        logger = self.logging_controllers[channel]
        error = controller.validate_inputs(
            active_area,
            start_density,
            stop_density,
            density_increment,
            voltage_compliance,
            dwell,
            interval,
            averaging_window,
            manual.voltage_range_name,
        )
        if error is not None:
            self._blocked_start(
                channel, self.polarisation_panels[channel],
                "Steady-State Polarisation", error,
            )
            return
        run_destination = self._choose_run_csv(
            channel, "polarisation", reuse_active_logger=True
        )
        if run_destination is None:
            return
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            selection = CurrentInputSelection.parse(
                CurrentInputMode.CURRENT_DENSITY.value, active_area
            )
            start_a = selection.resolve(start_density, "Start density").current_a
            stop_a = selection.resolve(stop_density, "End density").current_a
            step_a = selection.resolve(density_increment, "Density increment").current_a
            step_count = len(generate_inclusive_setpoints(
                start_density, stop_density, density_increment,
                axis_label="Current density",
            ))
            logger_path = (
                str(logger.snapshot.file_path)
                if logger.snapshot.active and logger.snapshot.file_path is not None
                else str(run_destination)
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Polarisation - CH{channel}",
                "REAL HARDWARE POWER-SUPPLY POLARISATION AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                f"Current density start / end / increment: {start_density} / "
                f"{stop_density} / {density_increment} A/cm²\n"
                f"Derived current start / end / increment: {float(start_a):g} / "
                f"{float(stop_a):g} / {float(step_a):g} A\n"
                f"Active area: {active_area} cm²\n"
                f"Voltage limit / compliance: {voltage_compliance} V\n"
                f"Dwell / interval / final averaging window: {dwell} / {interval} / "
                f"{averaging_window} s\n"
                f"Total steps: {step_count}\n"
                f"CSV destination: {logger_path}\n\n"
                "This is power-supply polarisation, not potentiostat control. Confirm "
                "the supervised load and wiring are safe. One guarded Output-On attempt "
                "will follow fresh identity/OFF/range/setpoint checks."
                + self._safety_confirmation_details(channel),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware polarisation cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_SCAN_CONFIRMATION
        if not controller.start(
            active_area,
            start_density,
            stop_density,
            density_increment,
            voltage_compliance,
            dwell,
            interval,
            averaging_window,
            manual.voltage_range_name,
            run_destination,
            logging_sink=logger if logger.snapshot.active else None,
            real_hardware_confirmation=confirmation,
        ):
            self.polarisation_panels[channel].show_validation_error(
                controller.snapshot.message
            )
            self.statusBar().showMessage(controller.snapshot.message)

    def _apply_protocol_snapshot(self, channel: int, snapshot: ProtocolSnapshot) -> None:
        self.channel_panels[channel].set_protocol_owned(
            self._channel_protocol_owned(channel)
        )
        self._update_protocol_context(channel)
        # Input/authorization rejection happens before ownership, file creation
        # or any output transition and is not a safety cleanup event.  Record
        # only runs which reached an owned/output-aware lifecycle.
        if (
            snapshot.state.value in {"COMPLETED", "FAILED"}
            and (snapshot.file_path is not None or snapshot.output_enabled is not None)
        ):
            fingerprint = (
                channel,
                snapshot.state.value,
                snapshot.message,
                str(snapshot.file_path),
            )
            if fingerprint not in self._logged_safety_events:
                self._logged_safety_events.add(fingerprint)
                self.safety_event_log.append(
                    channel=channel,
                    cause=snapshot.message,
                    requested_action="Protocol terminal Output-Off cleanup",
                    confirmed_off=snapshot.output_enabled is False,
                )
        if snapshot.operator_warning:
            QMessageBox.critical(
                self,
                f"Channel {channel} output state unconfirmed",
                snapshot.message,
            )

    def _start_custom_sequence(
        self,
        channel: int,
        sequence_name: str,
        stages: tuple[SequenceStage, ...],
        transition_policy: str = SequenceTransitionPolicy.SAFE_OFF.value,
        handoff_settling_s: str = "1.0",
    ) -> None:
        controller = self.sequence_controllers[channel]
        if any(
            item.snapshot.owns_channel
            for item in (
                self.protocol_controllers[channel],
                self.voltage_protocol_controllers[channel],
                self.voltage_scan_controllers[channel],
                self.current_scan_controllers[channel],
                self.polarisation_controllers[channel],
            )
        ):
            message = f"Stop the active CH{channel} protocol before starting a Custom Sequence."
            self.sequence_panels[channel].show_validation_error(message)
            self.statusBar().showMessage(message)
            return
        if self.logging_controllers[channel].snapshot.active:
            message = (
                f"Stop the CH{channel} standalone logger before starting a Custom Sequence; "
                "the sequence owns one raw CSV for its complete run."
            )
            self.sequence_panels[channel].show_validation_error(message)
            self.statusBar().showMessage(message)
            return
        manual = self.channel_controllers[channel].snapshot
        confirmation = None
        try:
            policy = SequenceTransitionPolicy(transition_policy)
            settling = float(handoff_settling_s)
            if not math.isfinite(settling) or not 0 <= settling <= 60:
                raise ValueError
        except (TypeError, ValueError):
            message = "Select a valid transition policy and finite handoff settling time."
            self._blocked_start(
                channel, self.sequence_panels[channel], "Custom Sequence", message
            )
            return
        try:
            prepared = controller.validate_sequence(
                sequence_name, stages, manual.voltage_range_name
            )
        except ValueError as error:
            self._blocked_start(
                channel, self.sequence_panels[channel],
                "Custom Sequence", str(error),
            )
            return
        run_destination = self._choose_run_csv(channel, "custom_sequence")
        if run_destination is None:
            return
        if not self._connection_controller.snapshot.simulation_mode:
            maximum_voltage, maximum_current = controller.sequence_maxima(prepared)
            stage_lines = "\n".join(
                f"{index}. {stage.stage_type.label}: "
                + ", ".join(f"{key}={value}" for key, value in stage.parameters)
                for index, stage in enumerate(stages, start=1)
            )
            current_states = ", ".join(
                f"CH{item.channel}={'ON' if item.output_enabled else 'OFF'}"
                for item in self._connection_controller.snapshot.channel_readbacks
            )
            transition_warning = (
                "Compatible CC/CV boundaries remain energized during handoff.\n"
                if policy is SequenceTransitionPolicy.CONTINUOUS
                else "Output is confirmed OFF between stages.\n"
            )
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Custom Sequence - CH{channel}",
                "REAL HARDWARE CUSTOM SEQUENCE OUTPUT AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                f"Sequence: {sequence_name.strip()}\n"
                f"Stages ({len(stages)}):\n{stage_lines}\n\n"
                f"Transition policy: {policy.label}\n"
                f"Continuous handoff settling: {settling:g} s\n"
                f"Maximum programmed voltage/current: "
                f"{maximum_voltage:g} V / {maximum_current:g} A\n"
                f"{transition_warning}"
                f"Displayed output states: {current_states}\n"
                f"CSV destination: {run_destination}\n\n"
                "Start will first run a fresh QPX600DP identity and atomic two-channel "
                "readback. The selected output must be OFF. Each stage will use its "
                "existing guarded transition; no Output-On action is retried. Confirm "
                "the 100-ohm load, limits and complete ordered stage list."
                + self._safety_confirmation_details(channel),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware Custom Sequence cancelled; no command submitted."
                )
                return
            confirmation = REAL_HARDWARE_SEQUENCE_CONFIRMATION
        if not controller.start(
            sequence_name,
            stages,
            manual.voltage_range_name,
            run_destination,
            real_hardware_confirmation=confirmation,
            transition_policy=policy,
            handoff_settling_s=handoff_settling_s,
        ):
            self.sequence_panels[channel].show_validation_error(controller.snapshot.message)
            self.statusBar().showMessage(controller.snapshot.message)

    def _resume_custom_sequence(self, channel: int) -> None:
        controller = self.sequence_controllers[channel]
        confirmation = None
        if not self._connection_controller.snapshot.simulation_mode:
            snapshot = controller.snapshot
            if not snapshot.resume_ready:
                return
            answer = QMessageBox.warning(
                self,
                f"Confirm REAL HARDWARE Custom Sequence Resume - CH{channel}",
                "REAL HARDWARE CUSTOM SEQUENCE RESUME AUTHORIZATION\n\n"
                f"Channel: CH{channel}\n"
                f"Sequence: {snapshot.sequence_name}\n"
                f"Current stage: {snapshot.stage_index}/{snapshot.stage_total} "
                f"({snapshot.stage_type})\n"
                "Resume reuses the existing guarded stage preflight and exactly one "
                "Output-On attempt. It will not restart or skip the stage."
                + self._safety_confirmation_details(
                    channel, file_path=snapshot.file_path
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(
                    f"CH{channel} Real Hardware Custom Sequence Resume cancelled."
                )
                return
            confirmation = REAL_HARDWARE_SEQUENCE_CONFIRMATION
        if not controller.resume(confirmation):
            self.sequence_panels[channel].show_validation_error(controller.snapshot.message)
            self.statusBar().showMessage(controller.snapshot.message)

    def _update_protocol_context(self, channel: int) -> None:
        if (
            channel not in self.protocol_panels
            or channel not in self.voltage_protocol_panels
            or channel not in self.voltage_scan_panels
            or channel not in self.current_scan_panels
            or channel not in self.polarisation_panels
            or channel not in self.sequence_panels
        ):
            return
        connection = self._connection_controller.snapshot
        manual = self.channel_controllers[channel].snapshot
        authoritative = next(
            (item for item in connection.channel_readbacks if item.channel == channel),
            None,
        )
        common_available = (
            connection.state.value == "connected"
            and authoritative is not None
            and authoritative.output_enabled is False
            and manual.connected
            and not manual.pending
            and not manual.faulted
        )
        self.protocol_panels[channel].set_context(
            available=(
                common_available
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
                and not self.polarisation_controllers[channel].snapshot.owns_channel
                and not self.sequence_controllers[channel].snapshot.owns_channel
            )
        )
        self.voltage_protocol_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
                and not self.polarisation_controllers[channel].snapshot.owns_channel
                and not self.sequence_controllers[channel].snapshot.owns_channel
            )
        )
        self.voltage_scan_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
                and not self.polarisation_controllers[channel].snapshot.owns_channel
                and not self.sequence_controllers[channel].snapshot.owns_channel
            ),
            simulation_mode=connection.simulation_mode,
        )
        self.current_scan_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.polarisation_controllers[channel].snapshot.owns_channel
                and not self.sequence_controllers[channel].snapshot.owns_channel
            ),
            simulation_mode=connection.simulation_mode,
        )
        self.polarisation_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
                and not self.sequence_controllers[channel].snapshot.owns_channel
            ),
            simulation_mode=connection.simulation_mode,
        )
        self.sequence_panels[channel].set_context(
            available=(
                common_available
                and not self.protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_protocol_controllers[channel].snapshot.owns_channel
                and not self.voltage_scan_controllers[channel].snapshot.owns_channel
                and not self.current_scan_controllers[channel].snapshot.owns_channel
                and not self.polarisation_controllers[channel].snapshot.owns_channel
                and not self.logging_controllers[channel].snapshot.active
            ),
            simulation_mode=connection.simulation_mode,
        )
        self._refresh_start_validation(channel)
        self._update_compact_channel_status(channel)

    def _update_compact_channel_status(self, channel: int) -> None:
        workspace = self.channel_workspaces.get(channel)
        if workspace is None:
            return
        manual = self.channel_controllers[channel].snapshot
        output = (
            "ON" if manual.output_enabled is True
            else "OFF" if manual.output_enabled is False else "UNKNOWN"
        )
        voltage = (
            f"{manual.measured_voltage_v:.4g} V"
            if manual.measured_voltage_v is not None else "—"
        )
        current = (
            f"{manual.measured_current_a:.4g} A"
            if manual.measured_current_a is not None else "—"
        )
        power = (
            f"{manual.measured_voltage_v * manual.measured_current_a:.4g} W"
            if manual.measured_voltage_v is not None
            and manual.measured_current_a is not None else "—"
        )
        controllers = (
            ("Chronopotentiometry (CP)", self.protocol_controllers[channel].snapshot),
            ("Chronoamperometry (CA)", self.voltage_protocol_controllers[channel].snapshot),
            ("Voltage Scan", self.voltage_scan_controllers[channel].snapshot),
            ("Current Scan", self.current_scan_controllers[channel].snapshot),
            ("Polarisation", self.polarisation_controllers[channel].snapshot),
            ("Custom Sequence", self.sequence_controllers[channel].snapshot),
        )
        active = next(
            ((name, snapshot) for name, snapshot in controllers if snapshot.owns_channel),
            None,
        )
        if active is None:
            protocol = "IDLE"
            stage = "—"
            protocol_path = None
        else:
            name, snapshot = active
            protocol = f"{name} / {snapshot.state.value}"
            protocol_path = snapshot.file_path
            if name == "Custom Sequence":
                stage = (
                    f"{snapshot.stage_index}/{snapshot.stage_total} {snapshot.stage_type}"
                )
            elif snapshot.scan_step_index is not None:
                stage = f"{snapshot.scan_step_index}/{snapshot.scan_total_steps}"
            else:
                stage = "—"
        logging = self.logging_controllers[channel].snapshot
        csv_path = protocol_path or logging.file_path
        csv_status = str(csv_path) if csv_path is not None else "inactive"
        workspace.update_status(
            output=output,
            voltage=voltage,
            current=current,
            power=power,
            protocol=protocol,
            stage=stage,
            csv=csv_status,
        )
        if hasattr(self, "live_data_page"):
            active_channels = {
                selected for selected in (1, 2)
                if self._channel_protocol_owned(selected)
                or self.logging_controllers[selected].snapshot.active
            }
            self.live_data_page.set_active_channels(active_channels)

    def _channel_protocol_owned(self, channel: int) -> bool:
        return (
            self.protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_protocol_controllers[channel].snapshot.owns_channel
            or self.voltage_scan_controllers[channel].snapshot.owns_channel
            or self.current_scan_controllers[channel].snapshot.owns_channel
            or self.polarisation_controllers[channel].snapshot.owns_channel
            or self.sequence_controllers[channel].snapshot.owns_channel
        )

    def _all_protocol_controllers(self):
        return (
            *self.protocol_controllers.values(),
            *self.voltage_protocol_controllers.values(),
            *self.voltage_scan_controllers.values(),
            *self.current_scan_controllers.values(),
            *self.polarisation_controllers.values(),
            *self.sequence_controllers.values(),
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
            self._save_persistent_settings()
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
        self._save_persistent_settings()
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
        self.always_visible_header.setProperty(
            "hardwareMode", "simulation" if snapshot.simulation_mode else "real"
        )
        self.always_visible_header.style().unpolish(self.always_visible_header)
        self.always_visible_header.style().polish(self.always_visible_header)
        for panel in self.channel_panels.values():
            panel.set_real_hardware_mode(not snapshot.simulation_mode)
        for channel in self.protocol_panels:
            self._update_protocol_context(channel)
        self.statusBar().showMessage(snapshot.message)

    def _show_connection_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        for channel in (1, 2):
            self.safety_event_log.append(
                channel=channel,
                cause=message,
                requested_action="Shared-session failure cleanup",
                confirmed_off=False,
            )
        QMessageBox.warning(self, "Connection problem", message)
