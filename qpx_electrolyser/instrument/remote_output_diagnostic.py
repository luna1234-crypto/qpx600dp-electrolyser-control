"""Minimal synchronous QPX600DP CH1 remote-output diagnostic.

This deliberately bypasses Qt, controllers, queues, CSV logging and plotting.
It never enumerates ports and performs at most one supervised run per process.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import sys
import time

from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.channel_readback import ChannelReadback, read_channel
from qpx_electrolyser.instrument.hardware_check import (
    MONITORED_OUTPUT_CONFIRMATION,
    WRITE_CONFIRMATION,
)
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport


CHANNEL = 1
VOLTAGE_V = 4.000
CURRENT_A = 0.080
REQUIRED_RANGE_NAME = "80 V"
RUN_DURATION_S = 10.0
POLL_INTERVAL_S = 1.0
CONFIRMATION_WINDOW_S = 1.0
CONFIRMATION_POLL_INTERVAL_S = 0.05


class RemoteOutputDiagnosticError(RuntimeError):
    """The one-shot diagnostic failed or could not confirm safe cleanup."""

    def __init__(self, message: str, report_path: Path | None = None) -> None:
        super().__init__(message)
        self.report_path = report_path


class RemoteOutputAuthorizationError(ValueError):
    """CLI authorization was rejected before constructing a serial backend."""


@dataclass(frozen=True, slots=True)
class DiagnosticSample:
    elapsed_s: float
    output_enabled: bool
    measured_voltage_v: float
    measured_current_a: float


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    report_path: Path
    samples: tuple[DiagnosticSample, ...]
    final_readback: ChannelReadback


class _RawDiagnosticReport:
    """Append timestamped notes and the transport's exact framed TX/RX records."""

    def __init__(self, directory: Path, now: datetime) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = directory / f"qpx600dp_remote_output_{stamp}.log"
        self._stream = self.path.open("x", encoding="utf-8", newline="\n")
        self._handler = logging.StreamHandler(self._stream)
        formatter = logging.Formatter(
            "%(asctime)sZ %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        self._handler.setFormatter(formatter)
        self._serial_logger = logging.getLogger(
            "qpx_electrolyser.instrument.serial_transport"
        )
        self._previous_level = self._serial_logger.level
        self._previous_propagate = self._serial_logger.propagate
        self._serial_logger.setLevel(logging.INFO)
        self._serial_logger.propagate = False
        self._serial_logger.addHandler(self._handler)
        self._closed = False

    def note(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self._stream.write(f"{timestamp} NOTE {message}\n")
        self._stream.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._serial_logger.removeHandler(self._handler)
        self._serial_logger.setLevel(self._previous_level)
        self._serial_logger.propagate = self._previous_propagate
        self._handler.flush()
        self._handler.close()
        self._stream.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot synchronous QPX600DP CH1 remote-output diagnostic. "
            "No port discovery, Qt, queues, CSV or background threads."
        )
    )
    parser.add_argument("--port", required=True, help="Explicit port; never auto-detected")
    parser.add_argument(
        "--timeout", type=float, default=1.0, help="Read/write timeout in seconds"
    )
    parser.add_argument(
        "--confirm-write",
        metavar=WRITE_CONFIRMATION,
        required=True,
        help="Exact existing token authorizing the fixed CH1 setting writes",
    )
    parser.add_argument(
        "--confirm-output-on",
        metavar=MONITORED_OUTPUT_CONFIRMATION,
        required=True,
        help="Exact existing strong token authorizing the single Output-On attempt",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=default_paths().log_directory,
        help="Directory for the timestamped raw diagnostic report",
    )
    return parser


def validate_authorization(args: argparse.Namespace) -> None:
    if not isinstance(args.port, str) or not args.port.strip():
        raise RemoteOutputAuthorizationError("--port must be an explicit nonempty name.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise RemoteOutputAuthorizationError("--timeout must be finite and positive.")
    if args.confirm_write != WRITE_CONFIRMATION:
        raise RemoteOutputAuthorizationError(
            f"Setting writes require --confirm-write {WRITE_CONFIRMATION}."
        )
    if args.confirm_output_on != MONITORED_OUTPUT_CONFIRMATION:
        raise RemoteOutputAuthorizationError(
            "Output On requires --confirm-output-on "
            f"{MONITORED_OUTPUT_CONFIRMATION}."
        )
    if not isinstance(args.report_dir, Path):
        raise RemoteOutputAuthorizationError("--report-dir must be a filesystem path.")


def _make_driver(port: str, timeout_s: float) -> QPX600DPDriver:
    return QPX600DPDriver(SerialTransport(
        port,
        timeout_s=timeout_s,
        write_timeout_s=timeout_s,
    ))


def _settings_match(actual: float, expected: float, resolution: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=resolution / 2 + 1e-12)


def _verify_initial_ch1(driver: QPX600DPDriver, report: _RawDiagnosticReport) -> None:
    voltage_range = driver.read_voltage_range(CHANNEL)
    voltage = driver.read_set_voltage(CHANNEL)
    current = driver.read_set_current(CHANNEL)
    if voltage_range.name != REQUIRED_RANGE_NAME:
        raise RemoteOutputDiagnosticError(
            f"CH1 must already be in {REQUIRED_RANGE_NAME} range; got {voltage_range.name}."
        )
    if not math.isfinite(voltage) or not 0 <= voltage <= voltage_range.max_voltage_v:
        raise RemoteOutputDiagnosticError("CH1 programmed voltage is invalid.")
    if not math.isfinite(current) or not 0 <= current <= QPX600DP_CAPABILITIES.max_current_a:
        raise RemoteOutputDiagnosticError("CH1 programmed current limit is invalid.")
    report.note(
        f"CH1 pre-write state verified: {voltage:.6g} V, {current:.6g} A, "
        f"range={voltage_range.name}"
    )


def _apply_and_verify_fixed_settings(
    driver: QPX600DPDriver, report: _RawDiagnosticReport
) -> None:
    driver.set_voltage(CHANNEL, VOLTAGE_V)
    driver.set_current(CHANNEL, CURRENT_A)
    voltage = driver.read_set_voltage(CHANNEL)
    current = driver.read_set_current(CHANNEL)
    voltage_resolution = QPX600DP_CAPABILITIES.voltage_range(
        REQUIRED_RANGE_NAME
    ).programming_resolution_v
    if not _settings_match(voltage, VOLTAGE_V, voltage_resolution):
        raise RemoteOutputDiagnosticError("CH1 voltage readback did not equal 4.000 V.")
    if not _settings_match(
        current,
        CURRENT_A,
        QPX600DP_CAPABILITIES.current_programming_resolution_a,
    ):
        raise RemoteOutputDiagnosticError("CH1 current-limit readback did not equal 0.080 A.")
    report.note("CH1 fixed settings verified: 4.000 V / 0.080 A")


def _confirm_stable_on(
    driver: QPX600DPDriver,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = monotonic() + CONFIRMATION_WINDOW_S
    driver.confirm_operation_complete()
    if monotonic() >= deadline or not driver.read_output_state(CHANNEL):
        raise RemoteOutputDiagnosticError("CH1 Output On did not remain confirmed.")
    remaining = deadline - monotonic()
    if remaining < CONFIRMATION_POLL_INTERVAL_S:
        raise RemoteOutputDiagnosticError("CH1 Output On stabilization timed out.")
    sleep(CONFIRMATION_POLL_INTERVAL_S)
    if monotonic() > deadline or not driver.read_output_state(CHANNEL):
        raise RemoteOutputDiagnosticError("CH1 Output On did not remain confirmed.")


def _confirm_stable_off(
    driver: QPX600DPDriver,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> ChannelReadback:
    deadline = monotonic() + CONFIRMATION_WINDOW_S
    driver.confirm_operation_complete()
    consecutive_off = 0
    first_poll = True
    while True:
        if not first_poll and deadline - monotonic() <= 1e-9:
            raise RemoteOutputDiagnosticError(
                "CH1 physical OFF could not be confirmed within 1.0 s."
            )
        first_poll = False
        if driver.read_output_state(CHANNEL):
            consecutive_off = 0
        else:
            consecutive_off += 1
            if consecutive_off == 2:
                break
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RemoteOutputDiagnosticError(
                "CH1 physical OFF could not be confirmed within 1.0 s."
            )
        sleep(min(CONFIRMATION_POLL_INTERVAL_S, remaining))
    readback = read_channel(driver, CHANNEL)
    if readback.output_enabled:
        raise RemoteOutputDiagnosticError(
            "CH1 did not remain OFF during authoritative readback."
        )
    return readback


def _monitor_once_per_second(
    driver: QPX600DPDriver,
    report: _RawDiagnosticReport,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    emit: Callable[[str], None],
) -> tuple[DiagnosticSample, ...]:
    started_at = monotonic()
    samples: list[DiagnosticSample] = []
    sample_total = int(RUN_DURATION_S / POLL_INTERVAL_S)
    for index in range(1, sample_total + 1):
        due_at = started_at + index * POLL_INTERVAL_S
        delay = due_at - monotonic()
        if delay > 0:
            sleep(delay)
        elapsed = monotonic() - started_at
        output_enabled = driver.read_output_state(CHANNEL)
        if not output_enabled:
            raise RemoteOutputDiagnosticError(
                f"CH1 unexpectedly reported OFF at {elapsed:.3f} s."
            )
        voltage = driver.read_voltage(CHANNEL)
        current = driver.read_current(CHANNEL)
        sample = DiagnosticSample(elapsed, True, voltage, current)
        samples.append(sample)
        line = (
            f"sample {index:02d}: elapsed={elapsed:.3f} s; OP1=ON; "
            f"V1O={voltage:.6g} V; I1O={current:.6g} A"
        )
        report.note(line)
        emit(line)
    return tuple(samples)


def run_diagnostic(
    args: argparse.Namespace,
    *,
    driver_factory: Callable[[str, float], QPX600DPDriver] = _make_driver,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    emit: Callable[[str], None] = print,
) -> DiagnosticResult:
    """Perform one run. Authorization is validated before backend construction."""
    validate_authorization(args)
    report = _RawDiagnosticReport(args.report_dir, utc_now())
    driver: QPX600DPDriver | None = None
    connected = False
    output_on_attempted = False
    output_off_attempted = False
    samples: tuple[DiagnosticSample, ...] = ()
    final_readback: ChannelReadback | None = None
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        report.note("one-shot diagnostic started; identity serial is redacted")
        driver = driver_factory(args.port.strip(), float(args.timeout))
        driver.connect()
        connected = True
        driver.identify()
        report.note("QPX600DP identity accepted; identity details redacted")

        op1 = driver.read_output_state(1)
        op2 = driver.read_output_state(2)
        if op1 or op2:
            raise RemoteOutputDiagnosticError(
                f"Both outputs must be OFF; observed OP1={int(op1)}, OP2={int(op2)}."
            )
        report.note("preflight confirmed OP1=0 and OP2=0")
        _verify_initial_ch1(driver, report)
        _apply_and_verify_fixed_settings(driver, report)

        output_on_attempted = True
        driver.output_on(CHANNEL)
        report.note("OP1 1 transmitted exactly once")
        _confirm_stable_on(driver, monotonic=monotonic, sleep=sleep)
        report.note("CH1 stable ON confirmed by *OPC? and two OP1? responses")
        samples = _monitor_once_per_second(
            driver,
            report,
            monotonic=monotonic,
            sleep=sleep,
            emit=emit,
        )
    except BaseException as error:
        primary_error = error
        report.note(f"run stopped: {str(error) or type(error).__name__}")
    finally:
        if driver is not None and output_on_attempted and not output_off_attempted:
            output_off_attempted = True
            try:
                driver.output_off(CHANNEL)
                report.note("OP1 0 transmitted exactly once for final cleanup")
                final_readback = _confirm_stable_off(
                    driver, monotonic=monotonic, sleep=sleep
                )
                report.note("CH1 stable OFF confirmed by authoritative readback")
            except BaseException as error:
                cleanup_error = error
                report.note(
                    "cleanup failed; physical OFF is unconfirmed: "
                    f"{str(error) or type(error).__name__}"
                )
        if driver is not None and connected:
            try:
                driver.disconnect()
                report.note("serial connection closed")
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                report.note(f"disconnect failed: {str(error) or type(error).__name__}")
        report.note("one-shot diagnostic finished; no retry was attempted")
        report_path = report.path
        report.close()

    if cleanup_error is not None:
        prefix = (
            f"{str(primary_error) or type(primary_error).__name__}; "
            if primary_error is not None
            else ""
        )
        raise RemoteOutputDiagnosticError(
            prefix
            + "Output-Off cleanup failed; physical OFF is unconfirmed: "
            + (str(cleanup_error) or type(cleanup_error).__name__),
            report_path,
        ) from cleanup_error
    if primary_error is not None:
        raise RemoteOutputDiagnosticError(
            str(primary_error) or type(primary_error).__name__, report_path
        ) from primary_error
    if final_readback is None:
        raise RemoteOutputDiagnosticError(
            "Diagnostic ended without an authoritative final OFF readback.", report_path
        )
    emit(f"Report: {report_path}")
    return DiagnosticResult(report_path, samples, final_readback)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_diagnostic(args)
    except (RemoteOutputAuthorizationError, RemoteOutputDiagnosticError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        report_path = getattr(error, "report_path", None)
        if report_path is not None:
            print(f"Report: {report_path}", file=sys.stderr)
        return 2
    print(
        f"Completed {len(result.samples)} samples; final OP1=0; "
        f"report={result.report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
