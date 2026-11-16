"""Read-only parsing model for previously recorded QPX600DP CSV data."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path

from qpx_electrolyser.data.csv_logging import READABLE_CSV_COLUMNS


class RecordedDataError(ValueError):
    """An imported file is malformed, ambiguous, or unsupported."""


class RecordedDataFormat(str, Enum):
    RAW = "raw"
    READABLE = "readable"


class RecordedPlotType(str, Enum):
    TIME = "time"
    VOLTAGE_CURRENT = "voltage_current"
    CURRENT_DENSITY_VOLTAGE = "current_density_voltage"


@dataclass(frozen=True, slots=True)
class RecordedPoint:
    elapsed_s: float
    voltage_v: float
    current_a: float
    protocol: str = ""
    stage_index: int | None = None
    stage_name: str = ""


@dataclass(frozen=True, slots=True)
class RecordedDataset:
    source_path: Path
    data_format: RecordedDataFormat
    points: tuple[RecordedPoint, ...]
    channel: int | None = None
    protocols: tuple[str, ...] = ()
    stage_indexes: tuple[int, ...] = ()
    active_area_cm2: float | None = None

    @property
    def channel_label(self) -> str:
        return f"CH{self.channel}" if self.channel in (1, 2) else "Channel unavailable"

    @property
    def supports_current_density(self) -> bool:
        return self.active_area_cm2 is not None

    @property
    def default_plot_type(self) -> RecordedPlotType:
        protocols = set(self.protocols)
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
    if not source.is_file():
        raise RecordedDataError(f"Recorded CSV does not exist: {source}")
    try:
        with source.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                raise RecordedDataError("CSV is empty or has no header row.")
            headers = tuple(reader.fieldnames)
            if len(headers) != len(set(headers)):
                raise RecordedDataError("CSV contains duplicate header names.")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise RecordedDataError(f"Could not read recorded CSV: {error}") from error

    readable = headers == READABLE_CSV_COLUMNS
    header_set = set(headers)
    raw = _RAW_REQUIRED.issubset(header_set) and bool(_RAW_IDENTIFIERS & header_set)
    if readable and raw:
        raise RecordedDataError("CSV headers are ambiguous between Raw and Readable formats.")
    if readable:
        return _parse_readable(source, rows)
    if raw:
        return _parse_raw(source, rows, header_set)
    expected = ", ".join(READABLE_CSV_COLUMNS)
    raise RecordedDataError(
        "Unsupported CSV header. Expected a QPX600DP Raw CSV or exactly: " + expected
    )


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
    return RecordedDataset(path, RecordedDataFormat.READABLE, tuple(points))


def _parse_raw(
    path: Path, rows: list[dict[str, str]], headers: set[str]
) -> RecordedDataset:
    points: list[RecordedPoint] = []
    channels: set[int] = set()
    protocols: set[str] = set()
    stages: set[int] = set()
    areas: set[float] = set()
    for row_number, row in enumerate(rows, start=2):
        status = (row.get("sample_status") or "").strip().lower()
        measurement_values = (
            (row.get("elapsed_seconds") or "").strip(),
            (row.get("measured_voltage_v") or "").strip(),
            (row.get("measured_current_a") or "").strip(),
        )
        # Terminal/error/transition rows legitimately carry no measurement.
        if not any(measurement_values):
            continue
        if not all(measurement_values):
            if status not in {"", "ok"}:
                continue
            raise RecordedDataError(
                f"Row {row_number}: Raw measurement row has incomplete numeric values."
            )
        elapsed = _number(measurement_values[0], row_number, "elapsed_seconds")
        voltage = _number(measurement_values[1], row_number, "measured_voltage_v")
        current = _number(measurement_values[2], row_number, "measured_current_a")
        _validate_sample(elapsed, voltage, current, row_number)
        if points and elapsed < points[-1].elapsed_s:
            raise RecordedDataError(
                f"Row {row_number}: elapsed_seconds is earlier than the preceding sample."
            )
        channel = _integer(row.get("channel"), row_number, "channel")
        if channel not in (1, 2):
            raise RecordedDataError(f"Row {row_number}: channel must be 1 or 2.")
        channels.add(channel)

        protocol = (row.get("protocol_name") or "").strip().lower()
        if (row.get("sequence_run_id") or "").strip() or (
            row.get("sequence_name") or ""
        ).strip():
            protocols.add("custom_sequence")
        elif protocol:
            protocols.add(protocol)
        stage_index = _optional_stage_index(row, row_number)
        if stage_index is not None:
            stages.add(stage_index)
        stage_name = (
            (row.get("sequence_stage_type") or "").strip()
            or (row.get("scan_name") or "").strip()
        )
        area_text = (row.get("active_area_cm2") or "").strip()
        if area_text:
            area = _number(area_text, row_number, "active_area_cm2")
            if area <= 0:
                raise RecordedDataError(
                    f"Row {row_number}: active_area_cm2 must be positive when present."
                )
            areas.add(area)
        points.append(RecordedPoint(
            elapsed, voltage, current, protocol, stage_index, stage_name
        ))

    if not points:
        raise RecordedDataError("Raw CSV contains no valid measurement rows.")
    if len(channels) != 1:
        raise RecordedDataError("Raw CSV contains ambiguous mixed-channel measurements.")
    if len(areas) > 1:
        raise RecordedDataError("Raw CSV contains conflicting active-area metadata.")
    area = next(iter(areas)) if areas else None
    return RecordedDataset(
        path,
        RecordedDataFormat.RAW,
        tuple(points),
        next(iter(channels)),
        tuple(sorted(protocols)),
        tuple(sorted(stages)),
        area,
    )


def _optional_stage_index(row: dict[str, str], row_number: int) -> int | None:
    for field in ("sequence_stage_index", "scan_step_index"):
        text = (row.get(field) or "").strip()
        if text:
            value = _integer(text, row_number, field)
            if value < 0:
                raise RecordedDataError(f"Row {row_number}: {field} cannot be negative.")
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
