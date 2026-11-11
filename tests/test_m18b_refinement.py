"""Focused M18B templates and operator-selected raw CSV regressions."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog
import pytest
from qpx_electrolyser.data.csv_logging import CsvLogFile
from qpx_electrolyser.data.sequence_templates import (
    SequenceTemplate, load_sequence_template, save_sequence_template,
)
from qpx_electrolyser.engine.custom_sequence import SequenceStage, SequenceStageType
from qpx_electrolyser.app.connection_controller import ConnectionController, ConnectionState
from qpx_electrolyser.gui.main_window import MainWindow
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_explicit_csv_path_is_exclusive_and_locked(tmp_path: Path) -> None:
    requested = tmp_path / "operator_selected.csv"
    writer = CsvLogFile(requested, 1)
    assert writer.path == requested
    writer.close()
    with pytest.raises(FileExistsError):
        CsvLogFile(requested, 1)


def test_csv_dialog_cancel_precedes_protocol_mutation(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SimulatedPowerSupply()
    connection = ConnectionController(lambda: backend)
    window = MainWindow(tmp_path / "application.log", connection, data_directory=tmp_path)
    window._force_native_csv_dialog = True
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    connection.request_connect()
    deadline = __import__("time").monotonic() + 2
    while __import__("time").monotonic() < deadline:
        qapp.processEvents()
        if connection.snapshot.state is ConnectionState.CONNECTED:
            break
    try:
        before = window.channel_controllers[1].snapshot
        window._start_voltage_protocol(1, "1", "0.1", "2", "0.25")
        qapp.processEvents()
        assert not window.voltage_protocol_controllers[1].snapshot.owns_channel
        assert window.channel_controllers[1].snapshot.output_enabled is before.output_enabled is False
        assert not list(tmp_path.glob("*.csv"))
    finally:
        window.close(); connection.shutdown(2)


def test_sequence_template_round_trip_preserves_offline_fields(tmp_path: Path) -> None:
    stage = SequenceStage.create(
        SequenceStageType.CONSTANT_CURRENT,
        current="0.1", voltage="2", duration="30", interval="1",
        current_input_mode="current_density", active_area_cm2="4",
    )
    template = SequenceTemplate(
        "CP then CA", "100-ohm supervised template", "continuous", "1.0", (stage,)
    )
    path = tmp_path / "example.qpxseq.txt"
    save_sequence_template(path, template)
    assert load_sequence_template(path) == template

