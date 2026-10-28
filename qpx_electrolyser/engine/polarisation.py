"""Steady-state power-supply polarisation built on the shared stepped scan."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import math
import statistics

from PySide6.QtCore import Signal, Slot

from qpx_electrolyser.data.csv_logging import CsvLogFile, ProtocolLogSink
from qpx_electrolyser.engine.constant_current import (
    DEFAULT_CONSTANT_CURRENT_LIMITS,
    ConstantCurrentSafetyLimits,
    ProtocolState,
)
from qpx_electrolyser.engine.current_input import CurrentInputMode
from qpx_electrolyser.engine.stepped_scan import (
    CurrentScanController,
    REAL_HARDWARE_SCAN_CONFIRMATION,
    SteppedScanConfig,
    _decimal,
)
from qpx_electrolyser.instrument.channel_readback import ChannelReadback
from qpx_electrolyser.instrument.exceptions import InstrumentValueError


@dataclass(frozen=True, slots=True)
class PolarisationPoint:
    """One completed steady-state step reduced from scheduled raw samples."""

    channel: int
    step_index: int
    total_steps: int
    commanded_current_a: float
    commanded_current_density_a_cm2: float
    mean_voltage_v: float
    population_voltage_sd_v: float
    mean_current_a: float
    mean_current_density_a_cm2: float
    sample_count: int
    active_start_s: float
    active_end_s: float


def summarize_polarisation_samples(
    samples: list[tuple[float, ChannelReadback]], active_area_cm2: float
) -> tuple[float, float, float, float, int, float, float]:
    """Return mean V, population V SD, mean I/j, count and active bounds."""
    if not samples:
        raise ValueError("polarisation summary requires at least one sample")
    if not math.isfinite(active_area_cm2) or active_area_cm2 <= 0:
        raise ValueError("polarisation active area must be finite and positive")
    voltages = [value.measured_voltage_v for _, value in samples]
    currents = [value.measured_current_a for _, value in samples]
    mean_current = statistics.fmean(currents)
    return (
        statistics.fmean(voltages),
        statistics.pstdev(voltages),
        mean_current,
        mean_current / active_area_cm2,
        len(samples),
        samples[0][0],
        samples[-1][0],
    )


class PolarisationController(CurrentScanController):
    """M14 adapter: M13 planning/safety plus a final-window reducer."""

    point_result_received = Signal(object)

    def __init__(
        self,
        channel: int,
        connection,
        parent=None,
        *,
        safety_limits: ConstantCurrentSafetyLimits = DEFAULT_CONSTANT_CURRENT_LIMITS,
        monotonic=None,
        utc_now=None,
        writer_factory=CsvLogFile,
    ) -> None:
        super().__init__(
            channel,
            connection,
            parent,
            safety_limits=safety_limits,
            monotonic=monotonic,
            utc_now=utc_now,
            writer_factory=writer_factory,
        )
        self._protocol_name = "steady_state_polarisation"
        self._display_name = "Steady-State Polarisation"
        self._command_slug = "polarisation"
        self._action_key = f"polarisation-action-{channel}"
        self._sample_key = f"polarisation-sample-{channel}"
        self._step_samples: list[tuple[float, ChannelReadback]] = []
        self._summary_metadata: dict[str, object] | None = None

    def validate_inputs(
        self,
        active_area_cm2: str,
        start_density: str,
        stop_density: str,
        density_increment: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        averaging_window: str,
        voltage_range_name: str | None,
    ) -> str | None:
        session_error = self._validate_scan_session(voltage_range_name)
        if session_error is not None:
            return session_error
        try:
            assert voltage_range_name is not None
            self._validate_polarisation_config(
                active_area_cm2,
                start_density,
                stop_density,
                density_increment,
                voltage_compliance,
                dwell,
                interval,
                averaging_window,
                voltage_range_name,
            )
        except (ValueError, InstrumentValueError) as error:
            return str(error)
        return None

    def start(
        self,
        active_area_cm2: str,
        start_density: str,
        stop_density: str,
        density_increment: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        averaging_window: str,
        voltage_range_name: str | None,
        directory: Path,
        *,
        logging_sink: ProtocolLogSink | None = None,
        real_hardware_confirmation: str | None = None,
    ) -> bool:
        if self._snapshot.owns_channel:
            return False
        validation_error = self.validate_inputs(
            active_area_cm2,
            start_density,
            stop_density,
            density_increment,
            voltage_compliance,
            dwell,
            interval,
            averaging_window,
            voltage_range_name,
        )
        if validation_error is not None:
            return self._reject(validation_error)
        if (
            not self._connection.snapshot.simulation_mode
            and real_hardware_confirmation != REAL_HARDWARE_SCAN_CONFIRMATION
        ):
            return self._reject("Real Hardware polarisation start was not confirmed.")
        assert voltage_range_name is not None
        config = self._validate_polarisation_config(
            active_area_cm2,
            start_density,
            stop_density,
            density_increment,
            voltage_compliance,
            dwell,
            interval,
            averaging_window,
            voltage_range_name,
        )
        self._scan_step_index = 0
        self._step_samples.clear()
        self._summary_metadata = None
        return self._begin_validated_start(config, directory, logging_sink=logging_sink)

    def _validate_polarisation_config(
        self,
        active_area_cm2: str,
        start_density: str,
        stop_density: str,
        density_increment: str,
        voltage_compliance: str,
        dwell: str,
        interval: str,
        averaging_window: str,
        voltage_range_name: str,
    ) -> SteppedScanConfig:
        base = super()._validate_scan_config(
            start_density,
            stop_density,
            density_increment,
            voltage_compliance,
            dwell,
            interval,
            voltage_range_name,
            current_input_mode=CurrentInputMode.CURRENT_DENSITY.value,
            active_area_cm2=active_area_cm2,
        )
        exact_dwell = _decimal(dwell, "Dwell time")
        exact_interval = _decimal(interval, "Sampling interval")
        exact_window = _decimal(averaging_window, "Final averaging window")
        if exact_window <= 0:
            raise ValueError("Final averaging window must be positive.")
        if exact_window > exact_dwell:
            raise ValueError("Final averaging window cannot exceed dwell time.")
        first_in_window = exact_dwell - exact_window
        available = 0
        sample_time = exact_interval
        while sample_time < exact_dwell:
            if sample_time >= first_in_window:
                available += 1
            sample_time += exact_interval
        if available < 1:
            raise ValueError(
                "Dwell time, sampling interval and final averaging window provide "
                "no completed scheduled sample for a summary point."
            )
        return replace(
            base,
            scan_name="steady_state_polarisation",
            averaging_window_s=float(exact_window),
        )

    def _protocol_csv_metadata(self) -> dict[str, object]:
        metadata = super()._protocol_csv_metadata()
        config = self._snapshot.config
        if isinstance(config, SteppedScanConfig) and config.scan_name == "steady_state_polarisation":
            metadata["polarisation_averaging_window_s"] = config.averaging_window_s
        if self._summary_metadata is not None:
            metadata.update(self._summary_metadata)
        return metadata

    def _command_completed(self, request_id: int, name: str, result: object) -> None:
        is_sample = (
            request_id == self._pending_request_id
            and self._pending_action == "sample"
            and isinstance(result, ChannelReadback)
        )
        sample_elapsed = self._elapsed_active() if is_sample else 0.0
        super()._command_completed(request_id, name, result)
        if is_sample and isinstance(result, ChannelReadback):
            config = self._snapshot.config
            if isinstance(config, SteppedScanConfig) and config.scan_name == "steady_state_polarisation":
                self._step_samples.append((sample_elapsed, result))

    @Slot()
    def _update_clock(self) -> None:
        """Route the final boundary through summary finalization before cleanup."""
        if self._snapshot.state not in {ProtocolState.RUNNING, ProtocolState.PAUSING}:
            return
        elapsed = self._elapsed_active()
        self._publish(replace(
            self._snapshot,
            elapsed_active_s=elapsed,
            remaining_active_s=self._remaining_active(elapsed),
        ))
        config = self._snapshot.config
        if (
            self._snapshot.state is ProtocolState.RUNNING
            and isinstance(config, SteppedScanConfig)
            and elapsed >= config.duration_s
            and self._pending_request_id is None
        ):
            self._sample_timer.start(0)

    def _advance_or_complete(self, config: SteppedScanConfig) -> None:
        if config.scan_name == "steady_state_polarisation":
            try:
                self._finalize_point(config)
            except (OSError, ValueError) as error:
                self._begin_cleanup(
                    ProtocolState.FAILED,
                    f"Polarisation summary CSV write failed: {error}",
                )
                return
        super()._advance_or_complete(config)

    def _finalize_point(self, config: SteppedScanConfig) -> None:
        step_start = self._scan_step_index * config.dwell_s
        step_end = step_start + config.dwell_s
        window_start = step_end - config.averaging_window_s
        selected = [
            (elapsed, value)
            for elapsed, value in self._step_samples
            if elapsed + 1e-12 >= window_start and elapsed < step_end + 1e-12
        ]
        if not selected:
            raise ValueError("completed step has no sample in its final averaging window")
        area = config.active_area_cm2
        if area is None or not math.isfinite(area) or area <= 0:
            raise ValueError("polarisation active area is unavailable")
        (
            mean_voltage,
            voltage_sd,
            mean_current,
            mean_density,
            sample_count,
            active_start,
            active_end,
        ) = summarize_polarisation_samples(selected, area)
        point = PolarisationPoint(
            channel=self._channel,
            step_index=self._scan_step_index + 1,
            total_steps=len(config.setpoints),
            commanded_current_a=config.setpoints[self._scan_step_index],
            commanded_current_density_a_cm2=(
                config.current_density_setpoints_a_cm2[self._scan_step_index]
            ),
            mean_voltage_v=mean_voltage,
            population_voltage_sd_v=voltage_sd,
            mean_current_a=mean_current,
            mean_current_density_a_cm2=mean_density,
            sample_count=sample_count,
            active_start_s=active_start,
            active_end_s=active_end,
        )
        last = selected[-1][1]
        summary = ChannelReadback(
            channel=self._channel,
            programmed_voltage_v=config.fixed_voltage_v,
            current_limit_a=point.commanded_current_a,
            measured_voltage_v=point.mean_voltage_v,
            measured_current_a=point.mean_current_a,
            voltage_range_name=last.voltage_range_name,
            output_enabled=last.output_enabled,
        )
        self._summary_metadata = {
            "measured_current_density_a_cm2": point.mean_current_density_a_cm2,
            "polarisation_voltage_sd_v": point.population_voltage_sd_v,
            "polarisation_sample_count": point.sample_count,
            "polarisation_active_start_s": point.active_start_s,
            "polarisation_active_end_s": point.active_end_s,
        }
        try:
            self._append(summary, "polarisation_summary", ProtocolState.RUNNING)
        finally:
            self._summary_metadata = None
        self._step_samples.clear()
        self.point_result_received.emit(point)
