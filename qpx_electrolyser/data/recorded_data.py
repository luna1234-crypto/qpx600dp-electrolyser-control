"""Read-only parsing model for previously recorded QPX600DP CSV data."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path

from qpx_electrolyser.data.csv_logging import READABLE_CSV_COLUMNS


_POLARISATION_SUMMARY_COLUMNS = (
    "step_index",
    "commanded_current_density_a_cm2",
    "commanded_current_a",
    "mean_measured_voltage_v",
    "population_voltage_sd_v",
    "mean_measured_current_a",
    "mean_measured_current_density_a_cm2",
    "sample_count",
    "active_start_s",
    "active_end_s",
)


class RecordedDataError(ValueError):
    """An imported file is malformed, ambiguous, or unsupported."""


class RecordedDataFormat(str, Enum):
    RAW = "raw"
    READABLE = "readable"


class RecordedPlotType(str, Enum):
    TIME = "time"
    VOLTAGE_CURRENT = "voltage_current"
    CURRENT_DENSITY_VOLTAGE = "current_density_voltage"
    POLARISATION = "polarisation"


@dataclass(frozen=True, slots=True)
class RecordedPoint:
    elapsed_s: float
    voltage_v: float
    current_a: float
    protocol: str = ""
    stage_index: int | None = None
    stage_name: str = ""
    sample_status: str = "ok"
    scan_point_valid: bool = False


@dataclass(frozen=True, slots=True)
class RecordedStage:
    index: int
    name: str
    start_elapsed_s: float | None
    end_elapsed_s: float | None
    completed: bool


@dataclass(frozen=True, slots=True)
class PolarisationSummaryPoint:
    step_index: int
    commanded_current_density_a_cm2: float
    commanded_current_a: float
    mean_voltage_v: float
    population_voltage_sd_v: float
    mean_current_a: float
    mean_current_density_a_cm2: float
    sample_count: int
    active_start_s: float
    active_end_s: float


@dataclass(frozen=True, slots=True)
class RecordedDataset:
    source_path: Path
    data_format: RecordedDataFormat
    points: tuple[RecordedPoint, ...]
    channel: int | None = None
    protocols: tuple[str, ...] = ()
    stage_indexes: tuple[int, ...] = ()
    active_area_cm2: float | None = None
    completed: bool = False
    stages: tuple[RecordedStage, ...] = ()
    polarisation_summary: tuple[PolarisationSummaryPoint, ...] = ()
    companion_path: Path | None = None
    advisories: tuple[str, ...] = ()

    @property
    def channel_label(self) -> str:
        return f"CH{self.channel}" if self.channel in (1, 2) else "Channel unavailable"

    @property
    def supports_current_density(self) -> bool:
        return self.active_area_cm2 is not None

    @property
    def is_custom_sequence(self) -> bool:
        return "custom_sequence" in self.protocols

    @property
    def xy_points(self) -> tuple[RecordedPoint, ...]:
        reduced = tuple(point for point in self.points if point.scan_point_valid)
        return reduced or self.points

    @property
    def default_plot_type(self) -> RecordedPlotType:
        protocols = set(self.protocols)
        if self.polarisation_summary:
            return RecordedPlotType.POLARISATION
        if "custom_sequence" in protocols or len(protocols) > 1:
            return RecordedPlotType.TIME
        if protocols & {
            "voltage_scan", "staircase_lsv", "current_scan", "polarisation",
            "steady_state_polarisation",
        }:
            if protocols & {"polarisation", "steady_state_polarisation"}:
                return (
                    RecordedPlotType.CURRENT_DENSITY_VOLTAGE
                    if self.supports_current_density
                    else RecordedPlotType.VOLTAGE_CURRENT
                )
            return RecordedPlotType.VOLTAGE_CURRENT
        return RecordedPlotType.TIME


_RAW_REQUIRED = {
    "elapsed_seconds", "channel", "measured_voltage_v", "measured_current_a",
}
_RAW_IDENTIFIERS = {"timestamp_iso", "sample_status", "protocol_name"}


def load_recorded_csv(path: Path) -> RecordedDataset:
    """Parse one supported CSV without modifying it or any live application state."""
    source = Path(path)
    headers, rows = _read_csv(source)
    readable = headers == READABLE_CSV_COLUMNS
    header_set = set(headers)
    raw = _RAW_REQUIRED.issubset(header_set) and bool(_RAW_IDENTIFIERS & header_set)
    if readable and raw:
        raise RecordedDataError("CSV headers are ambiguous between Raw and Readable formats.")
    if readable:
        return _parse_readable(source, rows)
    if raw:
        return _parse_raw(source, rows)
    expected = ", ".join(READABLE_CSV_COLUMNS)
    raise RecordedDataError(
        "Unsupported CSV header. Expected a QPX600DP Raw CSV or exactly: " + expected
    )


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    if not path.is_file():
        raise RecordedDataError(f"Recorded CSV does not exist: {path}")
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                raise RecordedDataError("CSV is empty or has no header row.")
            headers = tuple(reader.fieldnames)
            if len(headers) != len(set(headers)):
                raise RecordedDataError("CSV contains duplicate header names.")
            return headers, list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise RecordedDataError(f"Could not read recorded CSV: {error}") from error


def _parse_readable(path: Path, rows: list[dict[str, str]]) -> RecordedDataset:
    points: list[RecordedPoint] = []
    for row_number, row in enumerate(rows, start=2):
        elapsed = _number(row.get(READABLE_CSV_COLUMNS[0]), row_number, "Elapsed time (s)")
        voltage = _number(row.get(READABLE_CSV_COLUMNS[1]), row_number, "Voltage (V)")
        current = _number(row.get(READABLE_CSV_COLUMNS[2]), row_number, "Current (A)")
        _validate_sample(elapsed, voltage, current, row_number)
        if points and elapsed < points[-1].elapsed_s:
            raise RecordedDataError(
                f"Row {row_number}: elapsed time is earlier than the preceding row."
            )
        points.append(RecordedPoint(elapsed, voltage, current))
    if not points:
        raise RecordedDataError("Readable CSV contains no measurement rows.")
    return RecordedDataset(
        path, RecordedDataFormat.READABLE, tuple(points),
        advisories=(
            "Readable CSV has no protocol, channel, stage or active-area metadata; "
            "only generic time and V–I plots are available.",
        ),
    )


def _parse_raw(path: Path, rows: list[dict[str, str]]) -> RecordedDataset:
    points: list[RecordedPoint] = []
    channels: set[int] = set()
    protocols: set[str] = set()
    areas: set[float] = set()
    completed = False
    stage_names: dict[int, str] = {}
    stage_starts: dict[int, float] = {}
    stage_ends: dict[int, float] = {}
    completed_stages: set[int] = set()
    sequence_stage_totals: set[int] = set()
    for row_number, row in enumerate(rows, start=2):
        status = (row.get("sample_status") or "").strip().lower()
        protocol_state = (row.get("protocol_state") or "").strip().upper()
        stage_state = (row.get("sequence_stage_state") or "").strip().upper()
        if protocol_state == "COMPLETED":
            completed = True
        channel_text = (row.get("channel") or "").strip()
        if channel_text:
            channel = _integer(channel_text, row_number, "channel")
            if channel not in (1, 2):
                raise RecordedDataError(f"Row {row_number}: channel must be 1 or 2.")
            channels.add(channel)
        protocol = (row.get("protocol_name") or "").strip().lower()
        is_sequence = bool(
            (row.get("sequence_run_id") or "").strip()
            or (row.get("sequence_name") or "").strip()
        )
        if is_sequence:
            protocols.add("custom_sequence")
        elif protocol:
            protocols.add(protocol)
        stage_index = _optional_stage_index(row, row_number)
        sequence_stage_index_text = (
            row.get("sequence_stage_index") or ""
        ).strip()
        stage_total_text = (row.get("sequence_stage_total") or "").strip()
        sequence_stage_type = (row.get("sequence_stage_type") or "").strip()
        sequence_stage_index = (
            _integer(
                sequence_stage_index_text, row_number, "sequence_stage_index"
            )
            if sequence_stage_index_text
            else None
        )
        sequence_stage_total = (
            _integer(stage_total_text, row_number, "sequence_stage_total")
            if stage_total_text
            else None
        )
        is_actual_sequence_stage = is_sequence and bool(
            sequence_stage_type
            or (sequence_stage_index is not None and sequence_stage_index != 0)
            or (sequence_stage_total is not None and sequence_stage_total != 0)
        )
        if is_actual_sequence_stage:
            if sequence_stage_index is None:
                raise RecordedDataError(
                    f"Row {row_number}: sequence_stage_index is required for a "
                    "Custom Sequence stage."
                )
            if sequence_stage_total is None:
                raise RecordedDataError(
                    f"Row {row_number}: sequence_stage_total is required for a "
                    "Custom Sequence stage."
                )
            stage_total = sequence_stage_total
            if stage_total <= 0:
                raise RecordedDataError(
                    f"Row {row_number}: sequence_stage_total must be positive."
                )
            if not 1 <= sequence_stage_index <= stage_total:
                raise RecordedDataError(
                    f"Row {row_number}: sequence_stage_index must be between 1 "
                    f"and sequence_stage_total ({stage_total})."
                )
            sequence_stage_totals.add(stage_total)
        stage_name = (
            (row.get("sequence_stage_type") or "").strip()
            or (row.get("scan_name") or "").strip()
        )
        elapsed_text = (row.get("elapsed_seconds") or "").strip()
        elapsed = _number(elapsed_text, row_number, "elapsed_seconds") if elapsed_text else None
        if stage_index is not None:
            if stage_name:
                stage_names.setdefault(stage_index, stage_name)
            if elapsed is not None:
                stage_starts.setdefault(stage_index, elapsed)
                stage_ends[stage_index] = elapsed
            if status == "stage_completed" or stage_state == "COMPLETED":
                completed_stages.add(stage_index)
        area_text = (row.get("active_area_cm2") or "").strip()
        if area_text:
            area = _number(area_text, row_number, "active_area_cm2")
            if area <= 0:
                raise RecordedDataError(
                    f"Row {row_number}: active_area_cm2 must be positive when present."
                )
            areas.add(area)

        measurement_values = (
            elapsed_text,
            (row.get("measured_voltage_v") or "").strip(),
            (row.get("measured_current_a") or "").strip(),
        )
        if not any(measurement_values):
            continue
        if not all(measurement_values):
            if status not in {"", "ok", "lsv_point", "polarisation_summary"}:
                continue
            raise RecordedDataError(
                f"Row {row_number}: Raw measurement row has incomplete numeric values."
            )
        assert elapsed is not None
        voltage = _number(measurement_values[1], row_number, "measured_voltage_v")
        current = _number(measurement_values[2], row_number, "measured_current_a")
        _validate_sample(elapsed, voltage, current, row_number)
        if points and elapsed < points[-1].elapsed_s:
            raise RecordedDataError(
                f"Row {row_number}: elapsed_seconds is earlier than the preceding sample."
            )
        valid_text = (row.get("scan_point_valid") or "").strip().lower()
        scan_point_valid = status == "lsv_point" and valid_text in {"", "true", "1"}
        points.append(RecordedPoint(
            elapsed, voltage, current, protocol, stage_index, stage_name,
            status or "ok", scan_point_valid,
        ))

    if not points:
        raise RecordedDataError("Raw CSV contains no valid measurement rows.")
    if len(channels) != 1:
        raise RecordedDataError("Raw CSV contains ambiguous mixed-channel measurements.")
    if len(areas) > 1:
        raise RecordedDataError("Raw CSV contains conflicting active-area metadata.")
    if len(sequence_stage_totals) > 1:
        raise RecordedDataError("Raw CSV contains conflicting sequence stage totals.")
    if "custom_sequence" in protocols and sequence_stage_totals:
        completed = len(completed_stages) == next(iter(sequence_stage_totals))
    area = next(iter(areas)) if areas else None
    stages = tuple(
        RecordedStage(
            index, stage_names.get(index, ""), stage_starts.get(index),
            stage_ends.get(index), index in completed_stages,
        )
        for index in sorted(set(stage_names) | set(stage_starts) | completed_stages)
    )
    advisories: list[str] = []
    polarisation_protocol = bool(
        protocols & {"polarisation", "steady_state_polarisation"}
    )
    summary: tuple[PolarisationSummaryPoint, ...] = ()
    companion: Path | None = None
    if polarisation_protocol:
        summary, companion, companion_advisory = _load_polarisation_companion(path)
        if companion_advisory:
            advisories.append(companion_advisory)
        if area is None:
            advisories.append(
                "Active-area metadata is missing; current-density plots are unavailable."
            )
    elif area is None:
        advisories.append(
            "Active-area metadata is missing; current-density plots are unavailable."
        )
    if "custom_sequence" in protocols and not stages:
        advisories.append(
            "Custom Sequence stage metadata is missing; stage boundaries are unavailable."
        )
    if not completed:
        advisories.append(
            "No terminal COMPLETED metadata was found; valid basic measurements remain available."
        )
    return RecordedDataset(
        path, RecordedDataFormat.RAW, tuple(points), next(iter(channels)),
        tuple(sorted(protocols)), tuple(stage.index for stage in stages), area,
        completed, stages, summary, companion, tuple(advisories),
    )


def _load_polarisation_companion(
    raw_path: Path,
) -> tuple[tuple[PolarisationSummaryPoint, ...], Path | None, str]:
    exact = raw_path.with_name(f"{raw_path.stem}_polarisation_summary.csv")
    candidates = sorted(raw_path.parent.glob(
        f"{raw_path.stem}*_polarisation_summary.csv"
    ))
    if exact.is_file():
        selected = exact
    elif len(candidates) == 1:
        selected = candidates[0]
    elif len(candidates) > 1:
        return (), None, (
            "Multiple same-base polarisation summary companions were found; "
            "the completed-step mean/SD plot is unavailable."
        )
    else:
        return (), None, (
            "Polarisation summary companion is missing; basic time and V–I plots "
            "remain available, but the completed-step mean/SD plot is unavailable."
        )
    try:
        headers, rows = _read_csv(selected)
        if headers != _POLARISATION_SUMMARY_COLUMNS:
            raise RecordedDataError(
                "polarisation summary header does not match the application schema"
            )
        points: list[PolarisationSummaryPoint] = []
        for row_number, row in enumerate(rows, start=2):
            step = _integer(row.get("step_index"), row_number, "step_index")
            count = _integer(row.get("sample_count"), row_number, "sample_count")
            density = _number(row.get("commanded_current_density_a_cm2"), row_number, "commanded_current_density_a_cm2")
            commanded_current = _number(row.get("commanded_current_a"), row_number, "commanded_current_a")
            mean_voltage = _number(row.get("mean_measured_voltage_v"), row_number, "mean_measured_voltage_v")
            voltage_sd = _number(row.get("population_voltage_sd_v"), row_number, "population_voltage_sd_v")
            mean_current = _number(row.get("mean_measured_current_a"), row_number, "mean_measured_current_a")
            mean_density = _number(row.get("mean_measured_current_density_a_cm2"), row_number, "mean_measured_current_density_a_cm2")
            active_start = _number(row.get("active_start_s"), row_number, "active_start_s")
            active_end = _number(row.get("active_end_s"), row_number, "active_end_s")
            if step <= 0 or count <= 0 or voltage_sd < 0 or active_end < active_start:
                raise RecordedDataError(f"Row {row_number}: invalid summary bounds, count or SD.")
            points.append(PolarisationSummaryPoint(
                step, density, commanded_current, mean_voltage, voltage_sd,
                mean_current, mean_density, count, active_start, active_end,
            ))
        if not points:
            raise RecordedDataError("polarisation summary contains no completed steps")
        return tuple(points), selected, ""
    except RecordedDataError as error:
        return (), selected, (
            f"Polarisation summary companion is incompatible ({error}); basic plots "
            "remain available, but the completed-step mean/SD plot is unavailable."
        )


def _optional_stage_index(row: dict[str, str], row_number: int) -> int | None:
    for field in ("sequence_stage_index", "scan_step_index"):
        text = (row.get(field) or "").strip()
        if text:
            value = _integer(text, row_number, field)
            if value < 0:
                raise RecordedDataError(f"Row {row_number}: {field} cannot be negative.")
            if field == "sequence_stage_index" and value == 0:
                continue
            return value
    return None


def _number(value: str | None, row: int, field: str) -> float:
    try:
        result = float(value if value is not None else "")
    except (TypeError, ValueError) as error:
        raise RecordedDataError(f"Row {row}: {field} is not numeric.") from error
    if not math.isfinite(result):
        raise RecordedDataError(f"Row {row}: {field} must be finite.")
    return result


def _integer(value: str | None, row: int, field: str) -> int:
    number = _number(value, row, field)
    if not number.is_integer():
        raise RecordedDataError(f"Row {row}: {field} must be an integer.")
    return int(number)


def _validate_sample(elapsed: float, voltage: float, current: float, row: int) -> None:
    if elapsed < 0:
        raise RecordedDataError(f"Row {row}: elapsed time cannot be negative.")
    if voltage < 0 or current < 0:
        raise RecordedDataError(
            f"Row {row}: measured voltage and current cannot be negative."
        )
