"""Qt-independent M15 CSV validation, statistics, configuration and exports."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
from statistics import mean, pstdev
from typing import Iterable, Mapping

from qpx_electrolyser import __version__


STABLE_WINDOW_S = Decimal("30")
INSUFFICIENT_WINDOW = "Insufficient stable 30-second window"
MAX_SOURCE_ROWS = 2_000_000
REQUIRED_COLUMNS = {
    "timestamp_iso",
    "elapsed_seconds",
    "channel",
    "measured_voltage_v",
    "measured_current_a",
    "output_state",
    "sample_status",
    "error",
    "protocol_state",
    "sequence_run_id",
    "sequence_stage_index",
    "sequence_stage_type",
}


class AnalysisError(ValueError):
    """A source is not a compatible M15 stage-aware raw CSV."""


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    source_file: Path
    material_name: str = ""
    legend_label: str = ""
    active_area_override_cm2: Decimal | None = None


@dataclass(frozen=True, slots=True)
class StageResult:
    source_file: str
    material_name: str
    run_id: str
    channel: int
    stage_index: int
    stage_name: str
    commanded_mode: str
    commanded_setpoints: str
    active_area_cm2: Decimal | None
    active_area_source: str
    mean_voltage_v: Decimal | None
    voltage_population_sd_v: Decimal | None
    mean_current_a: Decimal | None
    current_population_sd_a: Decimal | None
    mean_current_density_a_cm2: Decimal | None
    sample_count: int
    analysis_start_elapsed_s: Decimal | None
    analysis_end_elapsed_s: Decimal | None
    analysis_start_timestamp: str
    analysis_end_timestamp: str
    validation_status: str

    @property
    def accepted(self) -> bool:
        return self.mean_voltage_v is not None and self.mean_current_a is not None


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    source_file: Path
    results: tuple[StageResult, ...]
    validation_status: str

    @property
    def accepted_points(self) -> tuple[StageResult, ...]:
        return tuple(item for item in self.results if item.accepted)


@dataclass(slots=True)
class CurveStyle:
    material_name: str = "Sample"
    legend_label: str = "Experiment"
    color: str = "#1769aa"
    marker: str = "circle"
    marker_size: float = 7.0
    line_style: str = "solid"
    line_width: float = 1.8
    visible: bool = True
    display_order: int = 0
    active_area_override_cm2: str = ""


@dataclass(slots=True)
class FigureSettings:
    title: str = "Steady-state polarisation comparison"
    x_label_current: str = "Mean measured current (A)"
    x_label_density: str = "Mean current density (A/cm²)"
    y_label: str = "Mean measured voltage (V)"
    legend_visible: bool = True
    grid_visible: bool = True
    error_bars_visible: bool = True
    width_in: float = 7.0
    height_in: float = 5.0
    dpi: int = 300
    output_filename: str = "m15_results"
    output_directory: str = ""
    formats: list[str] = field(default_factory=lambda: ["png", "svg", "pdf"])
    plot_mode: str = "current"


@dataclass(slots=True)
class PlotConfiguration:
    figure: FigureSettings = field(default_factory=FigureSettings)
    curves: dict[str, CurveStyle] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ValidRow:
    elapsed_s: Decimal
    timestamp: str
    voltage_v: Decimal
    current_a: Decimal
    area_cm2: Decimal | None
    programmed_voltage_v: str
    programmed_current_a: str
    scan_setpoint: str
    protocol_name: str


def _decimal(value: str, label: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise AnalysisError(f"{label} is not numeric") from error
    if not result.is_finite():
        raise AnalysisError(f"{label} is not finite")
    return result


def parse_active_area_override(value: str | Decimal | None) -> Decimal | None:
    if value is None or value == "":
        return None
    area = _decimal(str(value), "Active-area override")
    if area <= 0:
        raise AnalysisError("Active-area override must be positive")
    return area


def _valid_measurement(row: Mapping[str, str]) -> _ValidRow | None:
    if (
        row.get("sample_status") != "ok"
        or row.get("protocol_state") != "RUNNING"
        or row.get("output_state") != "ON"
        or bool(row.get("error", "").strip())
    ):
        return None
    try:
        elapsed = _decimal(row.get("elapsed_seconds", ""), "Elapsed time")
        voltage = _decimal(row.get("measured_voltage_v", ""), "Measured voltage")
        current = _decimal(row.get("measured_current_a", ""), "Measured current")
        if elapsed < 0 or voltage < 0 or current < 0:
            return None
        area_text = row.get("active_area_cm2", "").strip()
        area = _decimal(area_text, "Active area") if area_text else None
        if area is not None and area <= 0:
            area = None
    except AnalysisError:
        return None
    return _ValidRow(
        elapsed,
        row.get("timestamp_iso", ""),
        voltage,
        current,
        area,
        row.get("programmed_voltage_v", ""),
        row.get("programmed_current_limit_a", ""),
        row.get("scan_commanded_setpoint", ""),
        row.get("protocol_name", ""),
    )


def _commanded_setpoints(rows: Iterable[_ValidRow]) -> str:
    values: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.programmed_voltage_v, row.programmed_current_a, row.scan_setpoint)
        if key in seen:
            continue
        seen.add(key)
        values.append({
            "programmed_voltage_v": key[0],
            "programmed_current_a": key[1],
            "scan_commanded_setpoint": key[2],
        })
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _stage_completed(rows: Iterable[Mapping[str, str]]) -> bool:
    """Recognise both recorded M15 completion shapes without accepting Stop.

    Older and real-hardware sequence files do not always contain a separate
    ``stage_completed`` marker for the final stage.  The child protocol's
    natural terminal row is nevertheless unambiguous: it is ``completed`` in
    the COMPLETED state with the duration-completed reason.  Operator Stop
    uses the same status/state pair but a different reason and must remain
    excluded.
    """
    for row in rows:
        if row.get("sample_status", "").strip() == "stage_completed":
            return True
        if (
            row.get("sample_status", "").strip() == "completed"
            and row.get("protocol_state", "").strip() == "COMPLETED"
            and row.get("sequence_stage_state", "").strip() == "COMPLETED"
            and row.get("error", "").strip() == "Protocol duration completed"
        ):
            return True
    return False


def analyse_m15_csv(
    spec: ExperimentSpec,
    *,
    stable_window_s: Decimal = STABLE_WINDOW_S,
) -> AnalysisReport:
    """Read one immutable M15 raw CSV and produce one result per stage."""
    source = Path(spec.source_file)
    override = parse_active_area_override(spec.active_area_override_cm2)
    try:
        with source.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                raise AnalysisError("CSV is empty")
            missing = REQUIRED_COLUMNS.difference(reader.fieldnames)
            if missing:
                raise AnalysisError(
                    "Not a compatible M15 stage-aware raw CSV; missing: "
                    + ", ".join(sorted(missing))
                )
            rows: list[dict[str, str]] = []
            for row_number, row in enumerate(reader, start=1):
                if row_number > MAX_SOURCE_ROWS:
                    raise AnalysisError(
                        f"CSV exceeds the bounded {MAX_SOURCE_ROWS:,}-row analysis limit"
                    )
                rows.append(row)
    except OSError as error:
        raise AnalysisError(f"Cannot read source CSV: {error}") from error
    if not rows:
        raise AnalysisError("CSV contains no data rows")

    grouped: dict[tuple[str, int, int], list[dict[str, str]]] = {}
    for row in rows:
        run_id = row.get("sequence_run_id", "").strip()
        index_text = row.get("sequence_stage_index", "").strip()
        channel_text = row.get("channel", "").strip()
        if not run_id or not index_text or not channel_text:
            continue
        try:
            key = (run_id, int(channel_text), int(index_text))
        except ValueError:
            continue
        grouped.setdefault(key, []).append(row)
    if not grouped:
        raise AnalysisError("CSV contains no M15 sequence stage metadata")

    material = spec.material_name.strip() or source.stem
    results: list[StageResult] = []
    for (run_id, channel, stage_index), stage_rows in sorted(grouped.items()):
        stage_name = next(
            (row.get("sequence_stage_type", "") for row in stage_rows
             if row.get("sequence_stage_type", "")),
            "unknown",
        )
        completed = _stage_completed(stage_rows)
        segments: list[list[_ValidRow]] = []
        current_segment: list[_ValidRow] = []
        for row in stage_rows:
            valid = _valid_measurement(row)
            if valid is not None:
                current_segment.append(valid)
            elif current_segment:
                segments.append(current_segment)
                current_segment = []
        if current_segment:
            segments.append(current_segment)

        status = "Accepted"
        selected: list[_ValidRow] = []
        final_segment = segments[-1] if segments else []
        if not completed:
            status = "Stage not completed"
        elif not final_segment:
            status = INSUFFICIENT_WINDOW
        else:
            end = final_segment[-1].elapsed_s
            cutoff = end - stable_window_s
            if cutoff < 0 or final_segment[0].elapsed_s > cutoff:
                status = INSUFFICIENT_WINDOW
            else:
                selected = [row for row in final_segment if row.elapsed_s >= cutoff]
                if not selected:
                    status = INSUFFICIENT_WINDOW

        recorded_areas = [row.area_cm2 for row in selected if row.area_cm2 is not None]
        area = recorded_areas[-1] if recorded_areas else override
        area_source = "recorded" if recorded_areas else ("overridden" if override else "missing")
        if selected:
            voltages = [row.voltage_v for row in selected]
            currents = [row.current_a for row in selected]
            mean_voltage = mean(voltages)
            mean_current = mean(currents)
            voltage_sd = pstdev(voltages)
            current_sd = pstdev(currents)
            density = mean_current / area if area is not None else None
            if area is None:
                status = "Accepted; active area unavailable"
            start_elapsed = selected[0].elapsed_s
            end_elapsed = selected[-1].elapsed_s
            start_timestamp = selected[0].timestamp
            end_timestamp = selected[-1].timestamp
        else:
            mean_voltage = mean_current = voltage_sd = current_sd = density = None
            start_elapsed = end_elapsed = None
            start_timestamp = end_timestamp = ""
        command_rows = final_segment or [item for segment in segments for item in segment]
        results.append(StageResult(
            source_file=source.name,
            material_name=material,
            run_id=run_id,
            channel=channel,
            stage_index=stage_index,
            stage_name=stage_name,
            commanded_mode=stage_name,
            commanded_setpoints=_commanded_setpoints(command_rows),
            active_area_cm2=area,
            active_area_source=area_source,
            mean_voltage_v=mean_voltage,
            voltage_population_sd_v=voltage_sd,
            mean_current_a=mean_current,
            current_population_sd_a=current_sd,
            mean_current_density_a_cm2=density,
            sample_count=len(selected),
            analysis_start_elapsed_s=start_elapsed,
            analysis_end_elapsed_s=end_elapsed,
            analysis_start_timestamp=start_timestamp,
            analysis_end_timestamp=end_timestamp,
            validation_status=status,
        ))
    accepted = sum(item.accepted for item in results)
    issues = sorted({item.validation_status for item in results if not item.accepted})
    validation_status = f"{accepted}/{len(results)} completed stages accepted"
    if issues:
        validation_status += "; " + "; ".join(issues)
    return AnalysisReport(
        source_file=source,
        results=tuple(results),
        validation_status=validation_status,
    )


PROCESSED_COLUMNS = (
    "source_file", "material_sample_name", "sequence_run_id", "channel",
    "stage_index", "stage_name", "commanded_mode", "commanded_setpoints",
    "active_area_cm2", "active_area_source", "mean_measured_voltage_v",
    "voltage_population_sd_v", "mean_measured_current_a",
    "current_population_sd_a", "mean_current_density_a_cm2", "sample_count",
    "analysis_start_elapsed_s", "analysis_end_elapsed_s",
    "analysis_start_timestamp", "analysis_end_timestamp", "validation_status",
)


def sanitize_filename(value: str, default: str = "m15_results") -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or default


def exclusive_path(directory: Path, stem: str, suffix: str) -> Path:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Export directory does not exist: {directory}")
    safe = sanitize_filename(stem)
    candidate = directory / f"{safe}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{safe}_{index}{suffix}"
        index += 1
    return candidate


def export_processed_csv(
    reports: Iterable[AnalysisReport], directory: Path, stem: str
) -> Path:
    path = exclusive_path(directory, stem, "_processed.csv")
    try:
        with path.open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=PROCESSED_COLUMNS)
            writer.writeheader()
            for report in reports:
                for result in report.results:
                    writer.writerow({
                        "source_file": result.source_file,
                        "material_sample_name": result.material_name,
                        "sequence_run_id": result.run_id,
                        "channel": result.channel,
                        "stage_index": result.stage_index,
                        "stage_name": result.stage_name,
                        "commanded_mode": result.commanded_mode,
                        "commanded_setpoints": result.commanded_setpoints,
                        "active_area_cm2": _text(result.active_area_cm2),
                        "active_area_source": result.active_area_source,
                        "mean_measured_voltage_v": _text(result.mean_voltage_v),
                        "voltage_population_sd_v": _text(result.voltage_population_sd_v),
                        "mean_measured_current_a": _text(result.mean_current_a),
                        "current_population_sd_a": _text(result.current_population_sd_a),
                        "mean_current_density_a_cm2": _text(
                            result.mean_current_density_a_cm2
                        ),
                        "sample_count": result.sample_count,
                        "analysis_start_elapsed_s": _text(
                            result.analysis_start_elapsed_s
                        ),
                        "analysis_end_elapsed_s": _text(result.analysis_end_elapsed_s),
                        "analysis_start_timestamp": result.analysis_start_timestamp,
                        "analysis_end_timestamp": result.analysis_end_timestamp,
                        "validation_status": result.validation_status,
                    })
            stream.flush()
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _text(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def save_plot_configuration(
    configuration: PlotConfiguration, directory: Path, stem: str
) -> Path:
    path = exclusive_path(directory, stem, "_plot_config.json")
    payload = {
        "schema": "qpx600dp-m15b-plot-configuration-v1",
        "figure": asdict(configuration.figure),
        "curves": {key: asdict(value) for key, value in configuration.curves.items()},
    }
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return path


def load_plot_configuration(path: Path) -> PlotConfiguration:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != "qpx600dp-m15b-plot-configuration-v1":
            raise AnalysisError("Unsupported plot-configuration schema")
        figure = FigureSettings(**payload["figure"])
        curves = {key: CurveStyle(**value) for key, value in payload["curves"].items()}
        return PlotConfiguration(figure, curves)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, AnalysisError):
            raise
        raise AnalysisError(f"Cannot load plot configuration: {error}") from error


def processing_metadata(
    reports: Iterable[AnalysisReport], configuration: PlotConfiguration
) -> dict[str, object]:
    return {
        "schema": "qpx600dp-m15b-processing-v1",
        "stable_window_seconds": str(STABLE_WINDOW_S),
        "exclusion_rules": [
            "Output OFF", "Pause/Resume transitions", "handoff/settling",
            "starting/stopping/cleanup", "failed/error", "invalid measurements",
        ],
        "software_version": __version__,
        "processing_timestamp": datetime.now(timezone.utc).isoformat(),
        "source_filenames": [item.source_file.name for item in reports],
        "display_settings": asdict(configuration.figure),
        "curves": {key: asdict(value) for key, value in configuration.curves.items()},
    }


def export_processing_metadata(
    reports: Iterable[AnalysisReport],
    configuration: PlotConfiguration,
    directory: Path,
    stem: str,
) -> Path:
    path = exclusive_path(directory, stem, "_processing.json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            processing_metadata(reports, configuration),
            stream,
            indent=2,
            ensure_ascii=False,
        )
        stream.write("\n")
    return path
