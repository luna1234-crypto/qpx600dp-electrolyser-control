"""Per-channel Milestone 15 Custom Sequence editor and run status."""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from qpx_electrolyser.engine.constant_current import ProtocolState
from qpx_electrolyser.engine.custom_sequence import (
    SequenceSnapshot,
    SequenceStage,
    SequenceStageType,
)


_FIELDS: dict[SequenceStageType, tuple[tuple[str, str, str], ...]] = {
    SequenceStageType.CONSTANT_CURRENT: (
        ("current", "Target current (A):", "0.10"),
        ("voltage", "Voltage compliance (V):", "2"),
        ("duration", "Duration (s):", "2"),
        ("interval", "Sample interval (s):", "0.25"),
        ("current_input_mode", "Input mode (current/current_density):", "current"),
        ("active_area_cm2", "Active area (cm²):", "1"),
    ),
    SequenceStageType.CONSTANT_VOLTAGE: (
        ("voltage", "Target voltage (V):", "1.8"),
        ("current", "Current limit (A):", "0.20"),
        ("duration", "Duration (s):", "2"),
        ("interval", "Sample interval (s):", "0.25"),
    ),
    SequenceStageType.VOLTAGE_SCAN: (
        ("start", "Start voltage (V):", "1"),
        ("stop", "End voltage (V):", "2"),
        ("step_mv", "Signed step (mV):", "500"),
        ("rate_mv_s", "Scan rate (mV/s):", "500"),
        ("current", "Current limit (A):", "0.20"),
        ("settling", "Settling time (s):", "0.25"),
        ("measurements", "Measurements per point:", "2"),
        ("interval", "Sample interval (s):", "0.25"),
        ("active_area_cm2", "Active area (cm², optional):", ""),
    ),
    SequenceStageType.CURRENT_SCAN: (
        ("start", "Start current (A):", "0.10"),
        ("stop", "End current (A):", "0.20"),
        ("step", "Signed step (A):", "0.10"),
        ("voltage", "Voltage compliance (V):", "2"),
        ("dwell", "Dwell per step (s):", "1"),
        ("interval", "Sample interval (s):", "0.25"),
        ("current_input_mode", "Input mode (current/current_density):", "current"),
        ("active_area_cm2", "Active area (cm²):", "1"),
    ),
    SequenceStageType.POLARISATION: (
        ("active_area_cm2", "Active area (cm²):", "1"),
        ("start", "Start density (A/cm²):", "0.05"),
        ("stop", "End density (A/cm²):", "0.10"),
        ("step", "Signed increment (A/cm²):", "0.05"),
        ("voltage", "Voltage compliance (V):", "2"),
        ("dwell", "Dwell per step (s):", "2"),
        ("interval", "Sample interval (s):", "0.25"),
        ("window", "Final averaging window (s):", "1"),
    ),
    SequenceStageType.WAIT_OUTPUT_OFF: (
        ("duration", "Wait with output OFF (s):", "1"),
    ),
}


class CustomSequencePanel(QGroupBox):
    start_requested = Signal(str, object)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self, channel: int) -> None:
        super().__init__(f"Channel {channel} Custom Sequence")
        self.channel = channel
        self._available = False
        self._simulation_mode = True
        self._snapshot = SequenceSnapshot(channel=channel)
        self._stages: list[SequenceStage] = []

        self.name_input = QLineEdit(f"CH{channel} custom sequence")
        self.name_input.setObjectName(f"channel{channel}SequenceName")
        self.stage_list = QListWidget()
        self.stage_list.setObjectName(f"channel{channel}SequenceStages")
        self.stage_list.currentRowChanged.connect(self._load_selected)
        self.stage_type = QComboBox()
        for kind in SequenceStageType:
            self.stage_type.addItem(kind.label, kind.value)
        self.stage_type.currentIndexChanged.connect(self._rebuild_fields)
        self.field_labels: list[QLabel] = []
        self.field_inputs: list[QLineEdit] = []
        self.field_keys: list[str] = []
        editor = QGridLayout()
        editor.addWidget(QLabel("Stage type:"), 0, 0)
        editor.addWidget(self.stage_type, 0, 1)
        for row in range(1, 10):
            label, value = QLabel(), QLineEdit()
            editor.addWidget(label, row, 0)
            editor.addWidget(value, row, 1)
            self.field_labels.append(label)
            self.field_inputs.append(value)

        self.add_button = QPushButton("Add")
        self.update_button = QPushButton("Update")
        self.remove_button = QPushButton("Remove")
        self.duplicate_button = QPushButton("Duplicate")
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        edits = QHBoxLayout()
        for button in (
            self.add_button, self.update_button, self.remove_button,
            self.duplicate_button, self.up_button, self.down_button,
        ):
            edits.addWidget(button)
        self.add_button.clicked.connect(self._add)
        self.update_button.clicked.connect(self._update)
        self.remove_button.clicked.connect(self._remove)
        self.duplicate_button.clicked.connect(self._duplicate)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))

        self.start_button = QPushButton("Start Sequence")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        controls = QHBoxLayout()
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            controls.addWidget(button)
        self.start_button.clicked.connect(
            lambda: self.start_requested.emit(self.name_input.text(), tuple(self._stages))
        )
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)

        self.state_value = QLabel("IDLE")
        self.stage_value = QLabel("0/0")
        self.type_value = QLabel("Unavailable")
        self.progress_value = QLabel("0.000 s active; 0.000 s remaining")
        self.path_value = QLabel("Unavailable")
        self.path_value.setWordWrap(True)
        status = QGridLayout()
        for row, (label, widget) in enumerate((
            ("State:", self.state_value), ("Stage:", self.stage_value),
            ("Stage type:", self.type_value), ("Progress:", self.progress_value),
            ("Raw CSV:", self.path_value),
        )):
            status.addWidget(QLabel(label), row, 0)
            status.addWidget(widget, row, 1)
        self.message_label = QLabel("Custom Sequence idle")
        self.message_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        names = QHBoxLayout()
        names.addWidget(QLabel("Sequence name:"))
        names.addWidget(self.name_input, 1)
        layout.addLayout(names)
        layout.addWidget(self.stage_list)
        layout.addLayout(editor)
        layout.addLayout(edits)
        layout.addLayout(controls)
        layout.addLayout(status)
        layout.addWidget(self.message_label)
        self._rebuild_fields()
        self.apply_snapshot(self._snapshot)

    @property
    def stages(self) -> tuple[SequenceStage, ...]:
        return tuple(self._stages)

    def _current_kind(self) -> SequenceStageType:
        return SequenceStageType(str(self.stage_type.currentData()))

    def _rebuild_fields(self) -> None:
        fields = _FIELDS[self._current_kind()]
        self.field_keys = [key for key, _label, _default in fields]
        for index, (label, value) in enumerate(zip(self.field_labels, self.field_inputs)):
            if index < len(fields):
                _key, text, default = fields[index]
                label.setText(text)
                label.show()
                value.setText(default)
                value.show()
            else:
                label.hide()
                value.hide()

    def _editor_stage(self) -> SequenceStage:
        return SequenceStage.create(
            self._current_kind(),
            **{key: self.field_inputs[index].text() for index, key in enumerate(self.field_keys)},
        )

    @staticmethod
    def _describe(stage: SequenceStage) -> str:
        parameters = ", ".join(f"{key}={value}" for key, value in stage.parameters)
        return f"{stage.stage_type.label}: {parameters}"

    def _refresh_list(self, selected: int | None = None) -> None:
        self.stage_list.clear()
        self.stage_list.addItems([self._describe(stage) for stage in self._stages])
        if selected is not None and self._stages:
            self.stage_list.setCurrentRow(max(0, min(selected, len(self._stages) - 1)))
        self._update_enabled()

    def _add(self) -> None:
        self._stages.append(self._editor_stage())
        self._refresh_list(len(self._stages) - 1)

    def _update(self) -> None:
        row = self.stage_list.currentRow()
        if row >= 0:
            self._stages[row] = self._editor_stage()
            self._refresh_list(row)

    def _remove(self) -> None:
        row = self.stage_list.currentRow()
        if row >= 0:
            self._stages.pop(row)
            self._refresh_list(min(row, len(self._stages) - 1))

    def _duplicate(self) -> None:
        row = self.stage_list.currentRow()
        if row >= 0:
            self._stages.insert(row + 1, self._stages[row])
            self._refresh_list(row + 1)

    def _move(self, offset: int) -> None:
        row = self.stage_list.currentRow()
        target = row + offset
        if row >= 0 and 0 <= target < len(self._stages):
            self._stages[row], self._stages[target] = self._stages[target], self._stages[row]
            self._refresh_list(target)

    def _load_selected(self, row: int) -> None:
        if not (0 <= row < len(self._stages)):
            return
        stage = self._stages[row]
        index = self.stage_type.findData(stage.stage_type.value)
        self.stage_type.setCurrentIndex(index)
        values = stage.values()
        for field_index, key in enumerate(self.field_keys):
            self.field_inputs[field_index].setText(values.get(key, ""))

    @Slot(object)
    def apply_snapshot(self, snapshot: SequenceSnapshot) -> None:
        if snapshot.channel != self.channel:
            return
        self._snapshot = snapshot
        self.state_value.setText(snapshot.state.value)
        self.stage_value.setText(f"{snapshot.stage_index}/{snapshot.stage_total}")
        self.type_value.setText(snapshot.stage_type or "Unavailable")
        self.progress_value.setText(
            f"{snapshot.elapsed_active_s:.3f} s active; "
            f"{snapshot.remaining_active_s:.3f} s remaining"
        )
        self.path_value.setText("Unavailable" if snapshot.file_path is None else str(snapshot.file_path))
        self.message_label.setText(snapshot.message)
        self.message_label.setStyleSheet(
            "font-weight: 800; color: white; background: #b42318; padding: 4px;"
            if snapshot.operator_warning else ("color: #a61b1b;" if snapshot.error else "")
        )
        self._update_enabled()

    def set_context(self, *, available: bool, simulation_mode: bool) -> None:
        self._available = available
        self._simulation_mode = simulation_mode
        if not simulation_mode and not self._snapshot.owns_channel:
            self.message_label.setText(
                "Guarded Real Hardware Custom Sequence available; physical acceptance pending."
            )
        self._update_enabled()

    def show_validation_error(self, message: str) -> None:
        self.message_label.setText(message)
        self.message_label.setStyleSheet("color: #a61b1b;")

    def _update_enabled(self) -> None:
        state = self._snapshot.state
        editable = state in {ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED}
        for widget in (
            self.name_input, self.stage_list, self.stage_type, *self.field_inputs,
            self.add_button, self.update_button, self.remove_button,
            self.duplicate_button, self.up_button, self.down_button,
        ):
            widget.setEnabled(editable)
        self.start_button.setEnabled(
            editable and self._available and bool(self._stages)
        )
        self.pause_button.setEnabled(state is ProtocolState.RUNNING)
        self.resume_button.setEnabled(
            state is ProtocolState.PAUSED and self._snapshot.resume_ready and self._available
        )
        self.stop_button.setEnabled(state not in {
            ProtocolState.IDLE, ProtocolState.COMPLETED, ProtocolState.FAILED,
            ProtocolState.STOPPING,
        })
