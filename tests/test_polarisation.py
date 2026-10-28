"""M14 steady-state polarisation tests using simulator data only."""

from collections.abc import Callable
import csv
import os
from pathlib import Path
from threading import current_thread
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.data.csv_logging import CsvLogFile
from qpx_electrolyser.engine.polarisation import (
    PolarisationController,
    summarize_polarisation_samples,
)
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.gui.plots.polarisation import (
    BoundedPolarisationSeries, PolarisationPlotPoint,
)
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply
from qpx_electrolyser.instrument.simulator_config import SimulatorConfig
from qpx_electrolyser.instrument.channel_readback import ChannelReadback


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(qapp: QApplication, predicate: Callable[[], bool], timeout_s: float = 6) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for polarisation state")


class RecordingSimulator(SimulatedPowerSupply):
    def __init__(self) -> None:
        super().__init__(SimulatorConfig(communication_delay_s=0.001))
        self.calls: list[tuple[str, int, float | None, str]] = []

    def _record(self, name: str, channel: int, value: float | None = None) -> None:
        self.calls.append((name, channel, value, current_thread().name))

    def set_voltage(self, channel: int, value: float) -> None:
        self._record("set_voltage", channel, value); super().set_voltage(channel, value)

    def set_current(self, channel: int, value: float) -> None:
        self._record("set_current", channel, value); super().set_current(channel, value)

    def output_on(self, channel: int) -> None:
        self._record("output_on", channel); super().output_on(channel)

    def output_off(self, channel: int) -> None:
        self._record("output_off", channel); super().output_off(channel)

    def read_voltage(self, channel: int) -> float:
        self._record("read_voltage", channel); return super().read_voltage(channel)


def connected(qapp: QApplication) -> tuple[ConnectionController, RecordingSimulator]:
    backend = RecordingSimulator()
    connection = ConnectionController(lambda: backend)
    connection.request_connect()
    wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
    backend.calls.clear()
    return connection, backend


def start(controller: PolarisationController, directory: Path, **overrides: str) -> bool:
    values = {
        "area": "2", "first": "0.05", "last": "0.15", "increment": "0.05",
        "voltage": "2", "dwell": "0.4", "interval": "0.1", "window": "0.21",
    }
    values.update(overrides)
    return controller.start(
        values["area"], values["first"], values["last"], values["increment"],
        values["voltage"], values["dwell"], values["interval"], values["window"],
        "60 V", directory,
    )


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_decimal_density_planning_and_capacity_validation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    controller = PolarisationController(1, connection)
    try:
        config = controller._validate_polarisation_config(
            "2", "0.15", "0.05", "-0.04", "2", "1", "0.25", "0.5", "60 V"
        )
        assert config.current_density_setpoints_a_cm2 == (0.15, 0.11, 0.07, 0.05)
        assert config.setpoints == (0.3, 0.22, 0.14, 0.1)
        assert config.averaging_window_s == 0.5
        for invalid in (("0", "0.5"), ("1.1", "0.5"), ("0.1", "0.6")):
            assert controller.validate_inputs(
                "2", "0.05", "0.15", "0.05", "2", "1", invalid[1], invalid[0], "60 V"
            ) is not None
        assert backend.calls == []
        assert not list(tmp_path.glob("*.csv"))
    finally:
        controller.shutdown(); assert connection.shutdown(1)


def test_known_mean_population_sd_density_and_active_bounds() -> None:
    values = [
        (0.8, ChannelReadback(1, 2, 0.3, 3, 0.1, "60 V", True)),
        (0.9, ChannelReadback(1, 2, 0.3, 5, 0.3, "60 V", True)),
    ]
    assert summarize_polarisation_samples(values, 2) == (
        4.0, 1.0, 0.2, 0.1, 2, 0.8, 0.9,
    )


def test_summary_file_failure_triggers_safe_cleanup(
    qapp: QApplication, tmp_path: Path,
) -> None:
    class FailingSummaryWriter(CsvLogFile):
        def append(self, **values) -> None:  # type: ignore[no-untyped-def]
            if values.get("status") == "polarisation_summary":
                raise OSError("summary disk failure")
            super().append(**values)

    connection, backend = connected(qapp)
    controller = PolarisationController(1, connection, writer_factory=FailingSummaryWriter)
    try:
        assert start(controller, tmp_path, last="0.05")
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.FAILED)
        assert "summary CSV write failed" in controller.snapshot.message
        assert [call[0] for call in backend.calls].count("output_off") == 1
        assert controller.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_completed_steps_write_one_flushed_summary_and_preserve_raw_rows(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    controller = PolarisationController(1, connection)
    points = []
    controller.point_result_received.connect(points.append)
    try:
        assert start(controller, tmp_path)
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED)
        data = rows(controller.snapshot.file_path)  # type: ignore[arg-type]
        summaries = [row for row in data if row["sample_status"] == "polarisation_summary"]
        raw = [row for row in data if row["sample_status"] == "ok"]
        assert len(summaries) == 3 == len(points)
        assert raw
        assert all(row["scan_name"] == "steady_state_polarisation" for row in summaries)
        assert [int(row["scan_step_index"]) for row in summaries] == [1, 2, 3]
        assert [float(row["commanded_current_density_a_cm2"]) for row in summaries] == [0.05, 0.1, 0.15]
        assert all(int(row["polarisation_sample_count"]) >= 1 for row in summaries)
        assert all(row["polarisation_voltage_sd_v"] for row in summaries)
        assert all(float(row["polarisation_active_end_s"]) >= float(row["polarisation_active_start_s"]) for row in summaries)
        assert [item[2] for item in backend.calls if item[0] == "set_current"] == [0.1, 0.2, 0.3]
        assert [item[0] for item in backend.calls].count("output_on") == 1
        assert [item[0] for item in backend.calls].count("output_off") == 1
    finally:
        controller.shutdown(); assert connection.shutdown(1)


def test_stop_during_partial_step_writes_no_summary(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, _backend = connected(qapp)
    controller = PolarisationController(1, connection)
    try:
        assert start(controller, tmp_path, dwell="2", interval="0.2", window="0.5")
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.RUNNING)
        controller.stop("operator stop")
        wait_until(qapp, lambda: controller.snapshot.state is ProtocolState.COMPLETED)
        assert not [row for row in rows(controller.snapshot.file_path) if row["sample_status"] == "polarisation_summary"]  # type: ignore[arg-type]
        assert controller.snapshot.output_enabled is False
    finally:
        assert connection.shutdown(1)


def test_pause_resume_same_step_file_and_dual_channel_isolation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection, backend = connected(qapp)
    ch1 = PolarisationController(1, connection)
    ch2 = PolarisationController(2, connection)
    try:
        assert start(ch1, tmp_path, dwell="0.8")
        assert start(ch2, tmp_path, first="0.15", last="0.05", increment="-0.05", dwell="0.8")
        wait_until(qapp, lambda: ch1.snapshot.sample_count >= 1)
        path = ch1.snapshot.file_path; step = ch1.snapshot.scan_step_index
        ch1.pause(); wait_until(qapp, lambda: ch1.snapshot.state is ProtocolState.PAUSED)
        paused = ch1.snapshot.elapsed_active_s
        time.sleep(0.1); qapp.processEvents()
        assert ch1.snapshot.elapsed_active_s == paused
        assert ch1.resume()
        wait_until(qapp, lambda: all(c.snapshot.state is ProtocolState.COMPLETED for c in (ch1, ch2)))
        assert ch1.snapshot.file_path == path and ch1.snapshot.scan_step_index >= step
        assert {call[1] for call in backend.calls if call[0] in {"set_current", "output_on", "output_off"}} == {1, 2}
        assert len([r for r in rows(ch1.snapshot.file_path) if r["sample_status"] == "polarisation_summary"]) == 3  # type: ignore[arg-type]
        assert len([r for r in rows(ch2.snapshot.file_path) if r["sample_status"] == "polarisation_summary"]) == 3  # type: ignore[arg-type]
    finally:
        ch1.shutdown(); ch2.shutdown(); assert connection.shutdown(1)


def test_bounded_polarisation_plot_has_no_backend_dependency() -> None:
    series = BoundedPolarisationSeries(16)
    for index in range(100):
        series.append(PolarisationPlotPoint(index / 100, index / 10, index + 1))
    assert len(series.points) <= 16
    assert series.total == 100
    assert series.points[-1].step_index == 100


def test_gui_panels_start_correct_channels_and_show_validation(
    qapp: QApplication, tmp_path: Path,
) -> None:
    connection = ConnectionController(lambda: RecordingSimulator())
    window = MainWindow(tmp_path / "app.log", connection, data_directory=tmp_path)
    try:
        connection.request_connect()
        wait_until(qapp, lambda: connection.snapshot.state is ConnectionState.CONNECTED)
        for channel in (1, 2):
            panel = window.polarisation_panels[channel]
            panel.stop_density_input.setText("0.01")
            panel.dwell_input.setText("0.3")
            panel.interval_input.setText("0.1")
            panel.averaging_input.setText("0.2")
            panel.start_button.click()
        wait_until(qapp, lambda: all(
            c.snapshot.state is ProtocolState.COMPLETED
            for c in window.polarisation_controllers.values()
        ))
        assert all(c.snapshot.file_path is not None for c in window.polarisation_controllers.values())
        panel = window.polarisation_panels[1]
        panel.increment_input.setText("0")
        panel.start_button.click(); qapp.processEvents()
        assert "must not be zero" in panel.message_label.text()
    finally:
        window.close(); assert connection.shutdown(1)
