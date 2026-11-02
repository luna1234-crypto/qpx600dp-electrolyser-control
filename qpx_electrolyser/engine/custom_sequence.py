"""Milestone 15 channel-local orchestration over existing protocol controllers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from pathlib import Path
import time
from typing import Callable, Mapping
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from qpx_electrolyser.app.connection_controller import (
    ConnectionController,
    ConnectionSnapshot,
    ConnectionState,
)
from qpx_electrolyser.data.csv_logging import CsvLogFile, ProtocolLogSink
from qpx_electrolyser.engine.constant_current import (
    DEFAULT_CONSTANT_CURRENT_LIMITS,
    ConstantCurrentController,
    ProtocolSnapshot,
    ProtocolState,
    REAL_HARDWARE_PROTOCOL_CONFIRMATION,
)
from qpx_electrolyser.engine.constant_voltage import (
    ConstantVoltageController,
    REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
)
from qpx_electrolyser.engine.current_input import CurrentInputMode
from qpx_electrolyser.engine.polarisation import PolarisationController
from qpx_electrolyser.engine.stepped_scan import (
    CurrentScanController,
    REAL_HARDWARE_SCAN_CONFIRMATION,
    VoltageScanController,
)
from qpx_electrolyser.instrument.channel_readback import ChannelReadback


REAL_HARDWARE_SEQUENCE_CONFIRMATION = "RUN-QPX600DP-CUSTOM-SEQUENCE"


class SequenceStageType(str, Enum):
    CONSTANT_CURRENT = "constant_current"
    CONSTANT_VOLTAGE = "constant_voltage"
    VOLTAGE_SCAN = "voltage_scan"
    CURRENT_SCAN = "current_scan"
    POLARISATION = "steady_state_polarisation"
    WAIT_OUTPUT_OFF = "wait_output_off"

    @property
    def label(self) -> str:
        return {
            self.CONSTANT_CURRENT: "Constant Current",
            self.CONSTANT_VOLTAGE: "Constant Voltage",
            self.VOLTAGE_SCAN: "Voltage Sweep / Staircase LSV",
            self.CURRENT_SCAN: "Current Scan",
            self.POLARISATION: "Steady-State Polarisation",
            self.WAIT_OUTPUT_OFF: "Wait / Output-Off",
        }[self]


@dataclass(frozen=True, slots=True)
class SequenceStage:
    """One immutable editor stage; values stay textual until shared validation."""

    stage_type: SequenceStageType
    parameters: tuple[tuple[str, str], ...]

    @classmethod
    def create(cls, stage_type: SequenceStageType | str, **parameters: str) -> "SequenceStage":
        kind = SequenceStageType(stage_type)
        return cls(kind, tuple((key, str(value)) for key, value in parameters.items()))

    def values(self) -> dict[str, str]:
        return dict(self.parameters)

    def value(self, key: str, default: str = "") -> str:
        return self.values().get(key, default)


@dataclass(frozen=True, slots=True)
class _PreparedStage:
    stage: SequenceStage
    duration_s: float


@dataclass(frozen=True, slots=True)
class SequenceSnapshot:
    channel: int
    state: ProtocolState = ProtocolState.IDLE
    sequence_name: str = ""
    run_id: str = ""
    stage_index: int = 0
    stage_total: int = 0
    stage_type: str = ""
    stage_state: str = "idle"
    elapsed_active_s: float = 0.0
    remaining_active_s: float = 0.0
    stage_elapsed_active_s: float = 0.0
    stage_remaining_active_s: float = 0.0
    file_path: Path | None = None
    measured_voltage_v: float | None = None
    measured_current_a: float | None = None
    output_enabled: bool | None = None
    message: str = "Custom Sequence idle"
    error: bool = False
    operator_warning: bool = False
    resume_ready: bool = False

    @property
    def owns_channel(self) -> bool:
        return self.state in {
            ProtocolState.STARTING,
            ProtocolState.RUNNING,
            ProtocolState.PAUSING,
            ProtocolState.PAUSED,
            ProtocolState.RESUMING,
            ProtocolState.STOPPING,
        }


class SequenceLogSink(ProtocolLogSink):
    """One polling-free raw CSV writer shared by every stage in one sequence."""

    def __init__(
        self,
        directory: Path,
        channel: int,
        sequence_name: str,
        run_id: str,
        session_id: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        writer_factory=CsvLogFile,
    ) -> None:
        self._channel = channel
        self._sequence_name = sequence_name
        self._run_id = run_id
        self._session_id = session_id
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._writer = writer_factory(Path(directory), channel)
        self._stage_index = 0
        self._stage_total = 0
        self._stage_type = ""

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def file_path(self) -> Path | None:
        writer = self._writer
        return None if writer is None else writer.path

    def set_stage(self, index: int, total: int, stage_type: str) -> None:
        self._stage_index = index
        self._stage_total = total
        self._stage_type = stage_type

    def append_protocol(
        self,
        readback: ChannelReadback | None,
        *,
        status: str,
        protocol_state: str,
        error: str = "",
        protocol_name: str = "custom_sequence",
        **metadata: object,
    ) -> None:
        writer = self._writer
        if writer is None:
            raise ValueError("Custom Sequence CSV writer is unavailable")
        from datetime import datetime, timezone

        writer.append(
            timestamp=datetime.now(timezone.utc),
            elapsed_s=max(0.0, self._monotonic() - self._started_at),
            channel=self._channel,
            readback=readback,
            connection_state="connected",
            session_id=int(metadata.pop("session_id", self._session_id) or self._session_id),
            status=status,
            error=error,
            protocol_name=protocol_name,
            protocol_state=protocol_state,
            sequence_name=self._sequence_name,
            sequence_run_id=self._run_id,
            sequence_stage_index=self._stage_index,
            sequence_stage_total=self._stage_total,
            sequence_stage_type=self._stage_type,
            sequence_stage_state=protocol_state,
            **metadata,
        )

    def append_event(self, status: str, stage_state: str, *, error: str = "") -> None:
        self.append_protocol(
            None,
            status=status,
            protocol_state=stage_state,
            error=error,
            protocol_name="custom_sequence",
        )

    def suspend_sampling(self) -> None:
        """There is no standalone sequence poller to suspend."""

    def resume_sampling(self) -> None:
        """There is no standalone sequence poller to resume."""

    def close(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()


class CustomSequenceController(QObject):
    """Advance validated stages while each existing controller owns its own stage."""

    snapshot_changed = Signal(object)
    sample_received = Signal(object)
    lsv_point_received = Signal(object)
    polarisation_point_received = Signal(object)

    def __init__(
        self,
        channel: int,
        connection: ConnectionController,
        parent: QObject | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sink_factory=SequenceLogSink,
    ) -> None:
        super().__init__(parent)
        if channel not in (1, 2):
            raise ValueError("channel must be 1 or 2")
        self._channel = channel
        self._connection = connection
        self._monotonic = monotonic
        self._sink_factory = sink_factory
        self._snapshot = SequenceSnapshot(channel=channel)
        self._prepared: tuple[_PreparedStage, ...] = ()
        self._stage_index = 0
        self._completed_active_s = 0.0
        self._session_id = 0
        self._generation = 0
        self._handled_terminal_generation = -1
        self._transition_token = 0
        self._stop_requested = False
        self._sink: SequenceLogSink | None = None
        self._active_controller = None
        self._active_config = None
        self._real_hardware = False
        self._wait_started_at = 0.0
        self._wait_before_s = 0.0
        self._wait_timer = QTimer(self)
        self._wait_timer.setSingleShot(True)
        self._wait_timer.timeout.connect(self._wait_completed)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(100)
        self._clock_timer.timeout.connect(self._update_clock)

        self._controllers: Mapping[SequenceStageType, object] = {
            SequenceStageType.CONSTANT_CURRENT: ConstantCurrentController(
                channel, connection, self, monotonic=monotonic
            ),
            SequenceStageType.CONSTANT_VOLTAGE: ConstantVoltageController(
                channel, connection, self, monotonic=monotonic
            ),
            SequenceStageType.VOLTAGE_SCAN: VoltageScanController(
                channel, connection, self, monotonic=monotonic
            ),
            SequenceStageType.CURRENT_SCAN: CurrentScanController(
                channel, connection, self, monotonic=monotonic
            ),
            SequenceStageType.POLARISATION: PolarisationController(
                channel, connection, self, monotonic=monotonic
            ),
        }
        for controller in self._controllers.values():
            controller.snapshot_changed.connect(
                lambda snapshot, selected=controller: self._child_snapshot(selected, snapshot)
            )
            controller.sample_received.connect(self.sample_received)
        self._controllers[SequenceStageType.VOLTAGE_SCAN].point_result_received.connect(
            self.lsv_point_received
        )
        self._controllers[SequenceStageType.POLARISATION].point_result_received.connect(
            self.polarisation_point_received
        )
        connection.snapshot_changed.connect(self._connection_changed)

    @property
    def snapshot(self) -> SequenceSnapshot:
        return self._snapshot

    def validate_sequence(
        self,
        sequence_name: str,
        stages: list[SequenceStage] | tuple[SequenceStage, ...],
        voltage_range_name: str | None,
    ) -> tuple[_PreparedStage, ...]:
        if self._connection.snapshot.state is not ConnectionState.CONNECTED:
            raise ValueError("Connect and initialize the selected instrument first.")
        if voltage_range_name is None:
            raise ValueError("Refresh the channel before starting a Custom Sequence.")
        name = sequence_name.strip()
        if not name:
            raise ValueError("Sequence name is required.")
        if any(character in name for character in "\r\n"):
            raise ValueError("Sequence name must be a single line.")
        if not stages:
            raise ValueError("Add at least one sequence stage before Start.")
        initial = next(
            (
                item for item in self._connection.snapshot.initial_readbacks
                if item.channel == self._channel
            ),
            None,
        )
        if initial is None or initial.output_enabled:
            raise ValueError("Selected channel output must be confirmed OFF before Start.")
        if not self._connection.snapshot.simulation_mode:
            parts = [
                part.strip()
                for part in (self._connection.snapshot.instrument_id or "").split(",")
            ]
            if len(parts) != 4 or parts[1].upper() != "QPX600DP":
                raise ValueError("Real Hardware sequence requires an identified QPX600DP session.")

        prepared = tuple(
            self._prepare_stage(index, stage, voltage_range_name)
            for index, stage in enumerate(stages, start=1)
        )
        total = sum(item.duration_s for item in prepared)
        if total > DEFAULT_CONSTANT_CURRENT_LIMITS.max_duration_s:
            raise ValueError(
                f"Total sequence active duration exceeds the "
                f"{DEFAULT_CONSTANT_CURRENT_LIMITS.max_duration_s:g} second project limit."
            )
        return prepared

    def _prepare_stage(
        self, index: int, stage: SequenceStage, voltage_range_name: str
    ) -> _PreparedStage:
        values = stage.values()
        controller = self._controllers.get(stage.stage_type)
        error: str | None = None
        config = None
        try:
            if stage.stage_type is SequenceStageType.CONSTANT_CURRENT:
                assert isinstance(controller, ConstantCurrentController)
                error = controller.validate_inputs(
                    values.get("current", ""), values.get("voltage", ""),
                    values.get("duration", ""), values.get("interval", ""),
                    voltage_range_name,
                    current_input_mode=values.get("current_input_mode", CurrentInputMode.CURRENT.value),
                    active_area_cm2=values.get("active_area_cm2", ""),
                )
                if error is None:
                    config = controller._validate_config(
                        values.get("current", ""), values.get("voltage", ""),
                        values.get("duration", ""), values.get("interval", ""),
                        voltage_range_name,
                        current_input_mode=values.get("current_input_mode", CurrentInputMode.CURRENT.value),
                        active_area_cm2=values.get("active_area_cm2", ""),
                    )
            elif stage.stage_type is SequenceStageType.CONSTANT_VOLTAGE:
                assert isinstance(controller, ConstantVoltageController)
                error = controller.validate_inputs(
                    values.get("voltage", ""), values.get("current", ""),
                    values.get("duration", ""), values.get("interval", ""),
                    voltage_range_name,
                )
                if error is None:
                    config = controller._validate_config(
                        values.get("current", ""), values.get("voltage", ""),
                        values.get("duration", ""), values.get("interval", ""),
                        voltage_range_name,
                    )
            elif stage.stage_type is SequenceStageType.VOLTAGE_SCAN:
                assert isinstance(controller, VoltageScanController)
                error = controller.validate_inputs(
                    values.get("start", ""), values.get("stop", ""),
                    values.get("step_mv", ""), values.get("rate_mv_s", ""),
                    values.get("current", ""), values.get("settling", ""),
                    values.get("measurements", ""), values.get("interval", ""),
                    voltage_range_name, values.get("active_area_cm2", ""),
                )
                if error is None:
                    config = controller._validate_scan_config(
                        values.get("start", ""), values.get("stop", ""),
                        values.get("step_mv", ""), values.get("rate_mv_s", ""),
                        values.get("current", ""), values.get("settling", ""),
                        values.get("measurements", ""), values.get("interval", ""),
                        voltage_range_name, values.get("active_area_cm2", ""),
                    )
            elif stage.stage_type is SequenceStageType.CURRENT_SCAN:
                assert isinstance(controller, CurrentScanController)
                mode = values.get("current_input_mode", CurrentInputMode.CURRENT.value)
                area = values.get("active_area_cm2", "")
                error = controller.validate_inputs(
                    values.get("start", ""), values.get("stop", ""),
                    values.get("step", ""), values.get("voltage", ""),
                    values.get("dwell", ""), values.get("interval", ""),
                    voltage_range_name, current_input_mode=mode, active_area_cm2=area,
                )
                if error is None:
                    config = controller._validate_scan_config(
                        values.get("start", ""), values.get("stop", ""),
                        values.get("step", ""), values.get("voltage", ""),
                        values.get("dwell", ""), values.get("interval", ""),
                        voltage_range_name, current_input_mode=mode, active_area_cm2=area,
                    )
            elif stage.stage_type is SequenceStageType.POLARISATION:
                assert isinstance(controller, PolarisationController)
                error = controller.validate_inputs(
                    values.get("active_area_cm2", ""), values.get("start", ""),
                    values.get("stop", ""), values.get("step", ""),
                    values.get("voltage", ""), values.get("dwell", ""),
                    values.get("interval", ""), values.get("window", ""),
                    voltage_range_name,
                )
                if error is None:
                    config = controller._validate_polarisation_config(
                        values.get("active_area_cm2", ""), values.get("start", ""),
                        values.get("stop", ""), values.get("step", ""),
                        values.get("voltage", ""), values.get("dwell", ""),
                        values.get("interval", ""), values.get("window", ""),
                        voltage_range_name,
                    )
            elif stage.stage_type is SequenceStageType.WAIT_OUTPUT_OFF:
                duration = float(values.get("duration", ""))
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError("Wait duration must be finite and positive.")
                if duration > DEFAULT_CONSTANT_CURRENT_LIMITS.max_duration_s:
                    raise ValueError("Wait duration exceeds the project duration limit.")
                return _PreparedStage(stage, duration)
            else:
                raise ValueError("Unsupported sequence stage type.")
        except (TypeError, ValueError) as validation_error:
            error = str(validation_error)
        if error is not None:
            raise ValueError(f"Stage {index} ({stage.stage_type.label}): {error}")
        assert config is not None
        return _PreparedStage(stage, float(config.duration_s))

    def start(
        self,
        sequence_name: str,
        stages: list[SequenceStage] | tuple[SequenceStage, ...],
        voltage_range_name: str | None,
        directory: Path,
        *,
        real_hardware_confirmation: str | None = None,
    ) -> bool:
        if self._snapshot.owns_channel:
            return False
        try:
            prepared = self.validate_sequence(sequence_name, stages, voltage_range_name)
        except ValueError as error:
            self._publish(replace(self._snapshot, message=str(error), error=True))
            return False
        real_hardware = not self._connection.snapshot.simulation_mode
        if real_hardware and real_hardware_confirmation != REAL_HARDWARE_SEQUENCE_CONFIRMATION:
            self._publish(replace(
                self._snapshot,
                message="Real Hardware Custom Sequence start was not confirmed.",
                error=True,
            ))
            return False
        run_id = uuid4().hex
        try:
            sink = self._sink_factory(
                Path(directory), self._channel, sequence_name.strip(), run_id,
                self._connection.snapshot.session_id,
                monotonic=self._monotonic,
            )
        except (OSError, ValueError) as error:
            self._publish(replace(
                self._snapshot,
                message=f"Cannot create Custom Sequence CSV: {error}",
                error=True,
            ))
            return False

        self._prepared = prepared
        self._stage_index = 0
        self._completed_active_s = 0.0
        self._session_id = self._connection.snapshot.session_id
        self._generation += 1
        self._handled_terminal_generation = -1
        self._transition_token += 1
        self._stop_requested = False
        self._sink = sink
        self._active_controller = None
        self._active_config = None
        self._real_hardware = real_hardware
        total = sum(item.duration_s for item in prepared)
        self._publish(SequenceSnapshot(
            channel=self._channel,
            state=ProtocolState.STARTING,
            sequence_name=sequence_name.strip(),
            run_id=run_id,
            stage_total=len(prepared),
            remaining_active_s=total,
            file_path=sink.file_path,
            output_enabled=False,
            message="Starting Custom Sequence stage 1...",
        ))
        try:
            sink.append_event("sequence_started", ProtocolState.STARTING.value)
        except (OSError, ValueError) as error:
            self._finish(ProtocolState.FAILED, f"Custom Sequence CSV write failed: {error}")
            return False
        self._start_stage(self._transition_token)
        return self._snapshot.owns_channel

    def _start_stage(self, token: int) -> None:
        if token != self._transition_token or not self._snapshot.owns_channel:
            return
        if self._stop_requested:
            self._finish(ProtocolState.COMPLETED, "Custom Sequence stopped by operator")
            return
        if self._stage_index >= len(self._prepared):
            self._finish(ProtocolState.COMPLETED, "Custom Sequence completed")
            return
        prepared = self._prepared[self._stage_index]
        stage = prepared.stage
        sink = self._sink
        if sink is None:
            self._finish(ProtocolState.FAILED, "Custom Sequence CSV writer is unavailable")
            return
        sink.set_stage(self._stage_index + 1, len(self._prepared), stage.stage_type.value)
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.STARTING,
            stage_index=self._stage_index + 1,
            stage_type=stage.stage_type.value,
            stage_state=ProtocolState.STARTING.value,
            stage_elapsed_active_s=0.0,
            stage_remaining_active_s=prepared.duration_s,
            message=f"Starting stage {self._stage_index + 1}/{len(self._prepared)}: "
                    f"{stage.stage_type.label}",
            error=False,
            resume_ready=False,
        ))
        try:
            sink.append_event("stage_started", ProtocolState.STARTING.value)
        except (OSError, ValueError) as error:
            self._finish(ProtocolState.FAILED, f"Custom Sequence CSV write failed: {error}")
            return
        if stage.stage_type is SequenceStageType.WAIT_OUTPUT_OFF:
            self._active_controller = None
            self._active_config = None
            self._wait_before_s = 0.0
            self._wait_started_at = self._monotonic()
            self._publish(replace(
                self._snapshot,
                state=ProtocolState.RUNNING,
                stage_state=ProtocolState.RUNNING.value,
                output_enabled=False,
                message="Wait / Output-Off stage running; output remains OFF",
            ))
            self._clock_timer.start()
            self._schedule_wait()
            return
        self._start_protocol_stage(prepared)

    def _start_protocol_stage(self, prepared: _PreparedStage) -> None:
        stage = prepared.stage
        values = stage.values()
        controller = self._controllers[stage.stage_type]
        self._active_controller = controller
        self._active_config = None
        self._handled_terminal_generation = -1
        sink = self._sink
        assert sink is not None
        range_name = next(
            item.voltage_range_name
            for item in self._connection.snapshot.initial_readbacks
            if item.channel == self._channel
        )
        directory = sink.file_path.parent if sink.file_path is not None else Path.cwd()
        confirmation = REAL_HARDWARE_SCAN_CONFIRMATION if self._real_hardware else None
        if stage.stage_type is SequenceStageType.CONSTANT_CURRENT:
            confirmation = REAL_HARDWARE_PROTOCOL_CONFIRMATION if self._real_hardware else None
            started = controller.start(
                values.get("current", ""), values.get("voltage", ""),
                values.get("duration", ""), values.get("interval", ""),
                range_name, directory,
                current_input_mode=values.get("current_input_mode", CurrentInputMode.CURRENT.value),
                active_area_cm2=values.get("active_area_cm2", ""),
                logging_sink=sink, real_hardware_confirmation=confirmation,
            )
        elif stage.stage_type is SequenceStageType.CONSTANT_VOLTAGE:
            confirmation = (
                REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION if self._real_hardware else None
            )
            started = controller.start(
                values.get("voltage", ""), values.get("current", ""),
                values.get("duration", ""), values.get("interval", ""),
                range_name, directory, logging_sink=sink,
                real_hardware_confirmation=confirmation,
            )
        elif stage.stage_type is SequenceStageType.VOLTAGE_SCAN:
            started = controller.start(
                values.get("start", ""), values.get("stop", ""),
                values.get("step_mv", ""), values.get("rate_mv_s", ""),
                values.get("current", ""), values.get("settling", ""),
                values.get("measurements", ""), values.get("interval", ""),
                range_name, directory, logging_sink=sink,
                real_hardware_confirmation=confirmation,
                active_area_cm2=values.get("active_area_cm2", ""),
            )
        elif stage.stage_type is SequenceStageType.CURRENT_SCAN:
            started = controller.start(
                values.get("start", ""), values.get("stop", ""),
                values.get("step", ""), values.get("voltage", ""),
                values.get("dwell", ""), values.get("interval", ""),
                range_name, directory,
                current_input_mode=values.get("current_input_mode", CurrentInputMode.CURRENT.value),
                active_area_cm2=values.get("active_area_cm2", ""),
                logging_sink=sink, real_hardware_confirmation=confirmation,
            )
        elif stage.stage_type is SequenceStageType.POLARISATION:
            started = controller.start(
                values.get("active_area_cm2", ""), values.get("start", ""),
                values.get("stop", ""), values.get("step", ""),
                values.get("voltage", ""), values.get("dwell", ""),
                values.get("interval", ""), values.get("window", ""),
                range_name, directory, logging_sink=sink,
                real_hardware_confirmation=confirmation,
            )
        else:
            started = False
        self._active_config = controller.snapshot.config
        if not started:
            self._finish(
                ProtocolState.FAILED,
                f"Stage {self._stage_index + 1} failed to start: {controller.snapshot.message}",
                warning=controller.snapshot.operator_warning,
            )

    @Slot(object)
    def _child_snapshot(self, controller: object, snapshot: ProtocolSnapshot) -> None:
        if controller is not self._active_controller or not self._snapshot.owns_channel:
            return
        if self._active_config is not None and snapshot.config is not self._active_config:
            return
        elapsed = self._completed_active_s + snapshot.elapsed_active_s
        total = sum(item.duration_s for item in self._prepared)
        self._publish(replace(
            self._snapshot,
            state=snapshot.state,
            stage_state=snapshot.state.value,
            elapsed_active_s=elapsed,
            remaining_active_s=max(0.0, total - elapsed),
            stage_elapsed_active_s=snapshot.elapsed_active_s,
            stage_remaining_active_s=snapshot.remaining_active_s,
            measured_voltage_v=snapshot.measured_voltage_v,
            measured_current_a=snapshot.measured_current_a,
            output_enabled=snapshot.output_enabled,
            message=snapshot.message,
            error=snapshot.error,
            operator_warning=snapshot.operator_warning,
            resume_ready=snapshot.resume_ready,
        ))
        if snapshot.state not in {ProtocolState.COMPLETED, ProtocolState.FAILED}:
            return
        if self._handled_terminal_generation == self._generation:
            return
        self._handled_terminal_generation = self._generation
        if snapshot.state is ProtocolState.FAILED:
            self._finish(
                ProtocolState.FAILED,
                f"Stage {self._stage_index + 1} failed: {snapshot.message}",
                warning=snapshot.operator_warning,
            )
            return
        if self._stop_requested:
            self._finish(ProtocolState.COMPLETED, "Custom Sequence stopped by operator")
            return
        self._complete_stage()

    def _complete_stage(self) -> None:
        prepared = self._prepared[self._stage_index]
        self._completed_active_s += prepared.duration_s
        sink = self._sink
        if sink is not None:
            try:
                sink.append_event("stage_completed", ProtocolState.COMPLETED.value)
            except (OSError, ValueError) as error:
                self._finish(ProtocolState.FAILED, f"Custom Sequence CSV write failed: {error}")
                return
        self._active_controller = None
        self._active_config = None
        self._stage_index += 1
        self._generation += 1
        self._transition_token += 1
        token = self._transition_token
        if self._stage_index >= len(self._prepared):
            self._finish(ProtocolState.COMPLETED, "Custom Sequence completed")
            return
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.STARTING,
            elapsed_active_s=self._completed_active_s,
            remaining_active_s=sum(item.duration_s for item in self._prepared) - self._completed_active_s,
            output_enabled=False,
            message=f"Advancing to stage {self._stage_index + 1}/{len(self._prepared)}...",
        ))
        QTimer.singleShot(0, lambda: self._start_stage(token))

    def _wait_elapsed(self) -> float:
        if self._wait_started_at:
            return self._wait_before_s + max(0.0, self._monotonic() - self._wait_started_at)
        return self._wait_before_s

    def _schedule_wait(self) -> None:
        duration = self._prepared[self._stage_index].duration_s
        remaining = max(0.0, duration - self._wait_elapsed())
        self._wait_timer.start(max(0, math.ceil(remaining * 1000)))

    @Slot()
    def _wait_completed(self) -> None:
        if (
            self._snapshot.state is not ProtocolState.RUNNING
            or self._prepared[self._stage_index].stage.stage_type
            is not SequenceStageType.WAIT_OUTPUT_OFF
        ):
            return
        if self._wait_elapsed() + 1e-9 < self._prepared[self._stage_index].duration_s:
            self._schedule_wait()
            return
        self._clock_timer.stop()
        self._wait_before_s = self._prepared[self._stage_index].duration_s
        self._wait_started_at = 0.0
        self._complete_stage()

    @Slot()
    def pause(self) -> None:
        if self._snapshot.state is not ProtocolState.RUNNING:
            return
        if self._active_controller is not None:
            self._active_controller.pause()
            return
        self._wait_timer.stop()
        self._wait_before_s = self._wait_elapsed()
        self._wait_started_at = 0.0
        self._clock_timer.stop()
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.PAUSED,
            stage_state=ProtocolState.PAUSED.value,
            output_enabled=False,
            resume_ready=True,
            message="Wait / Output-Off stage paused; output remains OFF",
        ))
        self._append_sequence_event("paused", ProtocolState.PAUSED.value)

    def resume(self, real_hardware_confirmation: str | None = None) -> bool:
        if self._snapshot.state is not ProtocolState.PAUSED or not self._snapshot.resume_ready:
            return False
        if self._real_hardware and real_hardware_confirmation != REAL_HARDWARE_SEQUENCE_CONFIRMATION:
            self._publish(replace(
                self._snapshot,
                message="Real Hardware Custom Sequence resume was not confirmed.",
                error=True,
            ))
            return False
        if self._active_controller is not None:
            stage_type = self._prepared[self._stage_index].stage.stage_type
            token = {
                SequenceStageType.CONSTANT_CURRENT: REAL_HARDWARE_PROTOCOL_CONFIRMATION,
                SequenceStageType.CONSTANT_VOLTAGE: REAL_HARDWARE_CONSTANT_VOLTAGE_CONFIRMATION,
            }.get(stage_type, REAL_HARDWARE_SCAN_CONFIRMATION)
            return bool(self._active_controller.resume(token if self._real_hardware else None))
        self._wait_started_at = self._monotonic()
        self._publish(replace(
            self._snapshot,
            state=ProtocolState.RUNNING,
            stage_state=ProtocolState.RUNNING.value,
            resume_ready=False,
            error=False,
            message="Wait / Output-Off stage resumed; output remains OFF",
        ))
        self._append_sequence_event("resumed", ProtocolState.RUNNING.value)
        self._clock_timer.start()
        self._schedule_wait()
        return True

    @Slot()
    def stop(self, message: str = "Custom Sequence stopped by operator") -> None:
        if not self._snapshot.owns_channel:
            return
        self._stop_requested = True
        self._transition_token += 1
        self._wait_timer.stop()
        self._clock_timer.stop()
        if self._active_controller is not None and self._active_controller.snapshot.owns_channel:
            self._publish(replace(
                self._snapshot,
                state=ProtocolState.STOPPING,
                resume_ready=False,
                message=message,
            ))
            self._active_controller.stop(message)
            return
        self._finish(ProtocolState.COMPLETED, message)

    def shutdown(self) -> None:
        if not self._snapshot.owns_channel:
            return
        self._stop_requested = True
        self._transition_token += 1
        if self._active_controller is not None and self._active_controller.snapshot.owns_channel:
            self._active_controller.shutdown()
        else:
            self._finish(ProtocolState.FAILED, "Application shutdown requested")

    def force_shutdown(self, message: str) -> None:
        if self._active_controller is not None and self._active_controller.snapshot.owns_channel:
            self._active_controller.force_shutdown(message)
        else:
            self._finish(ProtocolState.FAILED, message, warning=True)

    def _append_sequence_event(self, status: str, state: str) -> bool:
        sink = self._sink
        if sink is None:
            return False
        try:
            sink.append_event(status, state)
            return True
        except (OSError, ValueError) as error:
            if self._active_controller is not None and self._active_controller.snapshot.owns_channel:
                self._active_controller.force_shutdown(
                    f"Custom Sequence CSV write failed: {error}"
                )
            else:
                self._finish(
                    ProtocolState.FAILED, f"Custom Sequence CSV write failed: {error}"
                )
            return False

    def _finish(
        self, state: ProtocolState, message: str, *, warning: bool = False
    ) -> None:
        self._wait_timer.stop()
        self._clock_timer.stop()
        self._transition_token += 1
        sink, self._sink = self._sink, None
        close_error = None
        if sink is not None:
            try:
                sink.append_event(
                    "sequence_completed" if state is ProtocolState.COMPLETED else "sequence_failed",
                    state.value,
                    error="" if state is ProtocolState.COMPLETED else message,
                )
                sink.close()
            except (OSError, ValueError) as error:
                close_error = error
        if close_error is not None:
            state = ProtocolState.FAILED
            message = f"{message}; Custom Sequence CSV close failed: {close_error}"
        self._active_controller = None
        self._active_config = None
        self._publish(replace(
            self._snapshot,
            state=state,
            stage_state=state.value,
            output_enabled=False if not warning else self._snapshot.output_enabled,
            resume_ready=False,
            message=message,
            error=state is ProtocolState.FAILED,
            operator_warning=warning or close_error is not None,
        ))

    @Slot()
    def _update_clock(self) -> None:
        if self._snapshot.state is not ProtocolState.RUNNING or self._active_controller is not None:
            return
        stage_elapsed = self._wait_elapsed()
        total = sum(item.duration_s for item in self._prepared)
        elapsed = self._completed_active_s + stage_elapsed
        self._publish(replace(
            self._snapshot,
            elapsed_active_s=elapsed,
            remaining_active_s=max(0.0, total - elapsed),
            stage_elapsed_active_s=stage_elapsed,
            stage_remaining_active_s=max(
                0.0, self._prepared[self._stage_index].duration_s - stage_elapsed
            ),
        ))

    @Slot(object)
    def _connection_changed(self, snapshot: ConnectionSnapshot) -> None:
        if not self._snapshot.owns_channel:
            return
        if (
            snapshot.state is ConnectionState.CONNECTED
            and snapshot.session_id == self._session_id
        ):
            return
        if self._active_controller is None:
            self._finish(
                ProtocolState.FAILED,
                "Custom Sequence stopped because the connected session changed.",
                warning=True,
            )

    def _publish(self, snapshot: SequenceSnapshot) -> None:
        self._snapshot = snapshot
        self.snapshot_changed.emit(snapshot)
