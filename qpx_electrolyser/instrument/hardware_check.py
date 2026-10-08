"""Explicit-port QPX600DP commissioning helper; read-only unless strongly authorised.

This module never enumerates serial ports. Run with
``python -m qpx_electrolyser.instrument.hardware_check --help``.
"""

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import sys
import time

from qpx_electrolyser.app.paths import default_paths
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.capabilities import QPX600DP_CAPABILITIES
from qpx_electrolyser.instrument.serial_transport import SerialTransport


WRITE_CONFIRMATION = "WRITE-QPX600DP"
OUTPUT_ON_CONFIRMATION = "ENABLE-QPX600DP-OUTPUT"
MONITORED_OUTPUT_CONFIRMATION = "MONITOR-QPX600DP-OUTPUT"
STATUS_FORENSICS_CONFIRMATION = "CAPTURE-QPX600DP-STATUS-ONCE"
MAX_MONITORED_DURATION_S = 60.0
MAX_VOLTAGE_SETTLE_TIME_S = 5.0
MIN_POLL_INTERVAL_S = 0.05
MAX_POLL_INTERVAL_S = 1.0


class HardwareCheckAuthorizationError(ValueError):
    """A requested hardware mutation lacks explicit command-line authorization."""


class MonitoredOutputSafetyError(RuntimeError):
    """A monitored Output-On run stopped or could not prove a safe shutdown."""


class StatusForensicsSafetyError(RuntimeError):
    """A one-shot status capture could not proceed without consuming unsafe evidence."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "QPX600DP explicit-port hardware check. With no mutation option it performs "
            "documented read-only queries only; it never discovers ports."
        )
    )
    parser.add_argument("--port", required=True, help="Explicit COM/serial port; never auto-detected")
    parser.add_argument("--timeout", type=float, default=1.0, help="Read/write timeout in seconds")
    parser.add_argument("--channel", type=int, choices=(1, 2), help="Channel for a mutation")
    parser.add_argument("--set-voltage", type=float, help="Program voltage without enabling output")
    parser.add_argument("--set-current", type=float, help="Program current without enabling output")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--output-off", action="store_true", help="Explicitly command channel OFF")
    output_group.add_argument(
        "--output-on-for",
        type=float,
        metavar="SECONDS",
        help="Enable only after explicit setpoints and confirmations, then attempt OFF",
    )
    output_group.add_argument(
        "--monitor-output-for",
        type=float,
        metavar="SECONDS",
        help="Guarded, polled Output-On validation without changing programmed settings",
    )
    parser.add_argument(
        "--safety-max-voltage", type=float,
        help="Required explicit operator limit for --output-on-for",
    )
    parser.add_argument(
        "--safety-max-current", type=float,
        help="Required explicit operator limit for --output-on-for",
    )
    parser.add_argument(
        "--safety-max-power", type=float,
        help="Required explicit operator limit for --output-on-for",
    )
    parser.add_argument(
        "--confirm-write", metavar=WRITE_CONFIRMATION,
        help="Exact token required for any setting or output write",
    )
    parser.add_argument(
        "--confirm-output-on", metavar=OUTPUT_ON_CONFIRMATION,
        help="Second exact token required only for --output-on-for",
    )
    parser.add_argument("--expected-voltage", type=float, help="Required programmed voltage")
    parser.add_argument(
        "--expected-current-limit", type=float, help="Required programmed current limit"
    )
    parser.add_argument(
        "--max-measured-current", type=float, help="Immediate-stop measured-current threshold"
    )
    parser.add_argument(
        "--min-measured-voltage", type=float, help="Minimum acceptable loaded voltage"
    )
    parser.add_argument(
        "--max-measured-voltage", type=float, help="Maximum acceptable loaded voltage"
    )
    parser.add_argument(
        "--poll-interval", type=float, help="Monitored-output polling interval in seconds"
    )
    parser.add_argument(
        "--voltage-settle-time",
        type=float,
        metavar="SECONDS",
        help="Bounded startup interval before enforcing minimum measured voltage",
    )
    parser.add_argument(
        "--confirm-monitored-output-on",
        metavar=MONITORED_OUTPUT_CONFIRMATION,
        help="Separate exact token required for --monitor-output-for",
    )
    parser.add_argument(
        "--capture-status-once",
        metavar=STATUS_FORENSICS_CONFIRMATION,
        help=(
            "One-shot status forensics; consumes LSR1, LSR2, ESR, EER and QER "
            "after an OFF-only non-clearing preflight"
        ),
    )
    return parser


def validate_authorization(args: argparse.Namespace) -> None:
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise HardwareCheckAuthorizationError("--timeout must be a finite positive number.")
    if args.capture_status_once is not None:
        _validate_status_forensics_authorization(args)
        return
    monitored = args.monitor_output_for is not None
    mutations = (
        args.set_voltage is not None
        or args.set_current is not None
        or args.output_off
        or args.output_on_for is not None
        or monitored
    )
    if mutations and args.confirm_write != WRITE_CONFIRMATION:
        raise HardwareCheckAuthorizationError(
            f"Any write requires --confirm-write {WRITE_CONFIRMATION}."
        )
    if mutations and args.channel not in (1, 2):
        raise HardwareCheckAuthorizationError("Any write requires --channel 1 or 2.")
    if monitored:
        _validate_monitored_authorization(args)
        return
    if args.output_on_for is None:
        return
    if args.confirm_output_on != OUTPUT_ON_CONFIRMATION:
        raise HardwareCheckAuthorizationError(
            "Output On requires the separate --confirm-output-on "
            f"{OUTPUT_ON_CONFIRMATION} token."
        )
    if args.output_on_for <= 0:
        raise HardwareCheckAuthorizationError("--output-on-for must be positive.")
    if args.set_voltage is None or args.set_current is None:
        raise HardwareCheckAuthorizationError(
            "Output On requires explicit --set-voltage and --set-current in the same invocation."
        )
    safety_values = (
        args.safety_max_voltage,
        args.safety_max_current,
        args.safety_max_power,
    )
    if any(value is None or value <= 0 for value in safety_values):
        raise HardwareCheckAuthorizationError(
            "Output On requires positive --safety-max-voltage, --safety-max-current "
            "and --safety-max-power values."
        )
    if args.set_voltage > args.safety_max_voltage:
        raise HardwareCheckAuthorizationError("Voltage setpoint exceeds the explicit safety limit.")
    if args.set_current > args.safety_max_current:
        raise HardwareCheckAuthorizationError("Current setpoint exceeds the explicit safety limit.")
    if args.set_voltage * args.set_current > args.safety_max_power:
        raise HardwareCheckAuthorizationError(
            "Setpoint/compliance product exceeds the explicit safety power limit."
        )


def _validate_status_forensics_authorization(args: argparse.Namespace) -> None:
    if args.capture_status_once != STATUS_FORENSICS_CONFIRMATION:
        raise HardwareCheckAuthorizationError(
            "Status forensics requires the exact token "
            f"--capture-status-once {STATUS_FORENSICS_CONFIRMATION}."
        )
    conflicts = (
        args.channel is not None,
        args.set_voltage is not None,
        args.set_current is not None,
        args.output_off,
        args.output_on_for is not None,
        args.monitor_output_for is not None,
        args.confirm_write is not None,
        args.confirm_output_on is not None,
        args.confirm_monitored_output_on is not None,
        args.safety_max_voltage is not None,
        args.safety_max_current is not None,
        args.safety_max_power is not None,
        args.expected_voltage is not None,
        args.expected_current_limit is not None,
        args.max_measured_current is not None,
        args.min_measured_voltage is not None,
        args.max_measured_voltage is not None,
        args.poll_interval is not None,
        args.voltage_settle_time is not None,
    )
    if any(conflicts):
        raise HardwareCheckAuthorizationError(
            "Status forensics cannot be combined with channel, setting, output or "
            "monitored-output options."
        )


def _validate_monitored_authorization(args: argparse.Namespace) -> None:
    if args.confirm_monitored_output_on != MONITORED_OUTPUT_CONFIRMATION:
        raise HardwareCheckAuthorizationError(
            "Monitored Output On requires --confirm-monitored-output-on "
            f"{MONITORED_OUTPUT_CONFIRMATION}."
        )
    if args.confirm_output_on is not None:
        raise HardwareCheckAuthorizationError(
            "--confirm-output-on conflicts with monitored-output mode."
        )
    conflicting = (
        args.set_voltage is not None,
        args.set_current is not None,
        args.safety_max_voltage is not None,
        args.safety_max_current is not None,
        args.safety_max_power is not None,
    )
    if any(conflicting):
        raise HardwareCheckAuthorizationError(
            "Monitored-output mode does not accept setting writes or legacy safety limits."
        )
    required = {
        "duration": args.monitor_output_for,
        "expected voltage": args.expected_voltage,
        "expected current limit": args.expected_current_limit,
        "maximum measured current": args.max_measured_current,
        "minimum measured voltage": args.min_measured_voltage,
        "maximum measured voltage": args.max_measured_voltage,
        "poll interval": args.poll_interval,
        "voltage settling time": args.voltage_settle_time,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise HardwareCheckAuthorizationError(
            "Monitored-output mode requires " + ", ".join(missing) + "."
        )
    if any(not math.isfinite(float(value)) for value in required.values()):
        raise HardwareCheckAuthorizationError("Monitored-output values must be finite.")
    if not 0 < args.monitor_output_for <= MAX_MONITORED_DURATION_S:
        raise HardwareCheckAuthorizationError(
            f"Monitored duration must be positive and no more than {MAX_MONITORED_DURATION_S:g} s."
        )
    if not MIN_POLL_INTERVAL_S <= args.poll_interval <= MAX_POLL_INTERVAL_S:
        raise HardwareCheckAuthorizationError(
            f"Poll interval must be between {MIN_POLL_INTERVAL_S:g} and "
            f"{MAX_POLL_INTERVAL_S:g} s."
        )
    if not 0 <= args.voltage_settle_time <= MAX_VOLTAGE_SETTLE_TIME_S:
        raise HardwareCheckAuthorizationError(
            f"Voltage settling time must be non-negative and no more than "
            f"{MAX_VOLTAGE_SETTLE_TIME_S:g} s."
        )
    if args.voltage_settle_time > args.monitor_output_for:
        raise HardwareCheckAuthorizationError(
            "Voltage settling time cannot exceed the total monitored duration."
        )
    if args.expected_voltage < 0 or args.expected_current_limit <= 0:
        raise HardwareCheckAuthorizationError(
            "Expected voltage must be non-negative and expected current limit must be positive."
        )
    if args.max_measured_current <= 0:
        raise HardwareCheckAuthorizationError("Maximum measured current must be positive.")
    if args.min_measured_voltage < 0 or args.max_measured_voltage < 0:
        raise HardwareCheckAuthorizationError("Measured-voltage limits must be non-negative.")
    if args.min_measured_voltage > args.max_measured_voltage:
        raise HardwareCheckAuthorizationError(
            "Minimum measured voltage cannot exceed maximum measured voltage."
        )
    if args.expected_voltage > QPX600DP_CAPABILITIES.max_voltage_v:
        raise HardwareCheckAuthorizationError("Expected voltage exceeds QPX600DP capability.")
    if not (
        QPX600DP_CAPABILITIES.min_current_a
        <= args.expected_current_limit
        <= QPX600DP_CAPABILITIES.max_current_a
    ):
        raise HardwareCheckAuthorizationError(
            "Expected current limit is outside QPX600DP capability."
        )
    QPX600DP_CAPABILITIES.validate_current(args.expected_current_limit)
    if args.max_measured_current > args.expected_current_limit:
        raise HardwareCheckAuthorizationError(
            "Maximum measured current cannot exceed the expected current limit."
        )
    if args.max_measured_voltage > QPX600DP_CAPABILITIES.max_voltage_v:
        raise HardwareCheckAuthorizationError(
            "Maximum measured voltage exceeds QPX600DP capability."
        )
    if not args.min_measured_voltage <= args.expected_voltage <= args.max_measured_voltage:
        raise HardwareCheckAuthorizationError(
            "Expected voltage must lie inside the acceptable measured-voltage range."
        )


def _make_driver(port: str, timeout_s: float) -> QPX600DPDriver:
    return QPX600DPDriver(SerialTransport(
        port,
        timeout_s=timeout_s,
        write_timeout_s=timeout_s,
    ))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _emit_channel_state(
    driver: QPX600DPDriver,
    channel: int,
    emit: Callable[[str], None],
) -> None:
    line, _output_on = _read_channel_state(driver, channel)
    emit(line)


def _read_channel_state(
    driver: QPX600DPDriver,
    channel: int,
) -> tuple[str, bool]:
    voltage_range = driver.read_voltage_range(channel)
    set_voltage = driver.read_set_voltage(channel)
    set_current = driver.read_set_current(channel)
    output_on = driver.read_output_state(channel)
    measured_voltage = driver.read_voltage(channel)
    measured_current = driver.read_current(channel)
    return (
        f"CH{channel}: range={voltage_range.name}; "
        f"set={set_voltage:g} V/{set_current:g} A; "
        f"output={'ON' if output_on else 'OFF'}; "
        f"measured={measured_voltage:g} V/{measured_current:g} A",
        output_on,
    )


_STATUS_BYTE_BITS = (
    "LIM1: enabled CH1 limit event pending",
    "LIM2: enabled CH2 limit event pending",
    "unused",
    "unused",
    "MAV: response message available",
    "ESB: enabled standard event pending",
    "RQS/MSS: service request/master summary",
    "unused",
)
_STANDARD_EVENT_BITS = (
    "operation complete",
    "unused",
    "query error",
    "verify timeout",
    "execution error",
    "command error",
    "unused",
    "power on",
)
_LIMIT_STATUS_BITS = (
    "constant-voltage limit entered",
    "constant-current limit entered",
    "power limit entered",
    "over-voltage trip",
    "over-current trip",
    "sense trip",
    "fault trip requiring AC power cycle",
    "reserved",
)
_LIMIT_ENABLE_BITS = tuple(
    f"enable reporting for {meaning}" if meaning != "reserved" else meaning
    for meaning in _LIMIT_STATUS_BITS
)


def _decode_bits(value: int, meanings: tuple[str, ...]) -> str:
    return "; ".join(
        f"bit {bit}={(value >> bit) & 1} ({meaning})"
        for bit, meaning in enumerate(meanings)
    )


def _decode_execution_error(value: int) -> str:
    if value == 0:
        return "no error encountered"
    if 1 <= value <= 9:
        return "hardware error"
    return {
        100: "numeric value outside the permitted range",
        101: "recalled setup contains corrupted data",
        102: "requested setup store is empty",
        103: "command invalid in the present circumstances",
        200: "write attempted without interface authority",
    }.get(value, "undocumented execution error value")


def _decode_query_error(value: int) -> str:
    return {
        0: "no query error",
        1: "interrupted query",
        2: "deadlock query error",
        3: "unterminated query",
    }.get(value, "undocumented query error value")


def _redact_identity(identity: str) -> str:
    parts = [part.strip() for part in identity.split(",")]
    if len(parts) != 4 or parts[0] != "THURLBY THANDAR" or parts[1] != "QPX600DP":
        raise StatusForensicsSafetyError(
            "Identity is not an exact supported THURLBY THANDAR QPX600DP identity."
        )
    return f"{parts[0]}, {parts[1]}, [SERIAL REDACTED], {parts[3]}"


def _report_path(report_directory: Path, captured_at: str) -> Path:
    safe_timestamp = re.sub(r"[^0-9A-Za-z]+", "", captured_at)
    if not safe_timestamp:
        safe_timestamp = "unknown_time"
    return report_directory / f"qpx600dp_status_forensics_{safe_timestamp}.txt"


def _run_status_forensics(
    driver: QPX600DPDriver,
    *,
    emit: Callable[[str], None],
    timestamp: Callable[[], str],
    report_directory: Path,
) -> None:
    captured_at = timestamp()
    lines = [
        "QPX600DP one-shot status forensics",
        f"Captured at: {captured_at}",
    ]

    identity = _redact_identity(driver.identify())
    lines.append(f"*IDN? = {identity}")

    status_byte = driver.read_status_byte()
    lines.append(f"*STB? = {status_byte}; {_decode_bits(status_byte, _STATUS_BYTE_BITS)}")
    for channel in (1, 2):
        value = driver.read_limit_status_enable(channel)
        lines.append(
            f"LSE{channel}? = {value}; "
            f"{_decode_bits(value, _LIMIT_ENABLE_BITS)}"
        )
    event_enable = driver.read_standard_event_status_enable()
    lines.append(
        f"*ESE? = {event_enable}; "
        f"{_decode_bits(event_enable, _STANDARD_EVENT_BITS)}"
    )
    for channel in (1, 2):
        ovp = driver.read_over_voltage_trip_setting(channel)
        ocp = driver.read_over_current_trip_setting(channel)
        lines.append(f"OVP{channel}? = {ovp:g} V")
        lines.append(f"OCP{channel}? = {ocp:g} A")

    output_states: dict[int, bool] = {}
    for channel in (1, 2):
        line, output_on = _read_channel_state(driver, channel)
        output_states[channel] = output_on
        lines.append(line)

    report_directory.mkdir(parents=True, exist_ok=True)
    path = _report_path(report_directory, captured_at)
    for line in lines:
        emit(line)
    with path.open("x", encoding="utf-8", newline="\n") as report:
        for line in lines:
            report.write(line + "\n")
        report.flush()
        emit(f"Diagnostic report: {path}")

        if any(output_states.values()):
            stopped = "ABORTED: both outputs must be OFF before clear-on-read queries."
            emit(stopped)
            report.write(stopped + "\n")
            report.flush()
            raise StatusForensicsSafetyError(stopped)

        warning = (
            "WARNING: the next five queries consume and clear their respective evidence: "
            "LSR1?, LSR2?, *ESR?, EER?, QER?. Each will be read exactly once."
        )
        emit(warning)
        report.write(warning + "\n")
        report.flush()

        for channel in (1, 2):
            value = driver.read_limit_status_and_clear(channel)
            line = (
                f"LSR{channel}? = {value}; "
                f"{_decode_bits(value, _LIMIT_STATUS_BITS)}"
            )
            emit(line)
            report.write(line + "\n")
            report.flush()

        event_status = driver.read_standard_event_status_and_clear()
        line = (
            f"*ESR? = {event_status}; "
            f"{_decode_bits(event_status, _STANDARD_EVENT_BITS)}"
        )
        emit(line)
        report.write(line + "\n")
        report.flush()

        execution_error = driver.read_execution_error_and_clear()
        line = f"EER? = {execution_error}; {_decode_execution_error(execution_error)}"
        emit(line)
        report.write(line + "\n")
        report.flush()

        query_error = driver.read_query_error_and_clear()
        line = f"QER? = {query_error}; {_decode_query_error(query_error)}"
        emit(line)
        report.write(line + "\n")
        report.flush()


def _run_monitored_output(
    args: argparse.Namespace,
    driver: QPX600DPDriver,
    *,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    timestamp: Callable[[], str],
    emit: Callable[[str], None],
) -> None:
    identity = driver.identify()
    parts = [part.strip() for part in identity.split(",")]
    if len(parts) != 4 or parts[0] != "THURLBY THANDAR" or parts[1] != "QPX600DP":
        raise MonitoredOutputSafetyError("Identity is not an exact supported QPX600DP identity.")
    emit(f"Identification: {identity}")

    output_states = {
        1: driver.read_output_state(1),
        2: driver.read_output_state(2),
    }
    if any(output_states.values()):
        states = ", ".join(
            f"OP{channel}={'ON' if state else 'OFF'}"
            for channel, state in output_states.items()
        )
        raise MonitoredOutputSafetyError(f"Preflight requires both outputs OFF; {states}.")

    channel = args.channel
    programmed_voltage = driver.read_set_voltage(channel)
    programmed_current = driver.read_set_current(channel)
    voltage_range = driver.read_voltage_range(channel)
    measured_voltage = driver.read_voltage(channel)
    measured_current = driver.read_current(channel)
    expected_voltage = QPX600DP_CAPABILITIES.validate_voltage(
        args.expected_voltage, voltage_range.name
    )
    expected_current = QPX600DP_CAPABILITIES.validate_current(args.expected_current_limit)
    voltage_tolerance = voltage_range.programming_resolution_v / 2.0 + 1e-12
    current_tolerance = QPX600DP_CAPABILITIES.current_programming_resolution_a / 2.0 + 1e-12
    if not math.isclose(
        programmed_voltage, expected_voltage, rel_tol=0.0, abs_tol=voltage_tolerance
    ):
        raise MonitoredOutputSafetyError(
            f"Programmed voltage {programmed_voltage:g} V does not match expected "
            f"{expected_voltage:g} V within {voltage_tolerance:g} V."
        )
    if not math.isclose(
        programmed_current, expected_current, rel_tol=0.0, abs_tol=current_tolerance
    ):
        raise MonitoredOutputSafetyError(
            f"Current limit {programmed_current:g} A does not match expected "
            f"{expected_current:g} A within {current_tolerance:g} A."
        )
    emit(
        f"Preflight: OP1=OFF; OP2=OFF; CH{channel} range={voltage_range.name}; "
        f"set={programmed_voltage:g} V/{programmed_current:g} A; "
        f"measured={measured_voltage:g} V/{measured_current:g} A"
    )
    emit("Timestamp (UTC) | Elapsed (s) | Voltage (V) | Current (A) | Output | Phase")

    started = monotonic()
    deadline = started + args.monitor_output_for
    run_error: BaseException | None = None
    off_error: BaseException | None = None
    off_confirm_error: BaseException | None = None
    off_confirmed = False
    try:
        try:
            # The cleanup scope is active before this command is attempted because a
            # transport error may occur after some or all bytes have been transmitted.
            driver.output_on(channel)
            while True:
                output_on = driver.read_output_state(channel)
                voltage = driver.read_voltage(channel)
                current = driver.read_current(channel)
                elapsed = max(0.0, monotonic() - started)
                settling = elapsed < args.voltage_settle_time
                emit(
                    f"{timestamp()} | {elapsed:.3f} | {voltage:.6g} | "
                    f"{current:.6g} | {'ON' if output_on else 'OFF'} | "
                    f"{'SETTLING' if settling else 'MONITORING'}"
                )
                if not output_on:
                    raise MonitoredOutputSafetyError(
                        f"CH{channel} unexpectedly reported Output OFF during the run."
                    )
                if current >= args.max_measured_current:
                    raise MonitoredOutputSafetyError(
                        f"CH{channel} measured current {current:g} A reached or exceeded "
                        f"the {args.max_measured_current:g} A limit."
                    )
                if voltage > args.max_measured_voltage:
                    raise MonitoredOutputSafetyError(
                        f"CH{channel} measured voltage {voltage:g} V exceeds the "
                        f"{args.max_measured_voltage:g} V maximum."
                    )
                if not settling and voltage < args.min_measured_voltage:
                    raise MonitoredOutputSafetyError(
                        f"CH{channel} measured voltage {voltage:g} V is below the "
                        f"{args.min_measured_voltage:g} V minimum after settling."
                    )
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                sleep(min(args.poll_interval, remaining))
        except BaseException as error:
            run_error = error
        finally:
            try:
                driver.output_off(channel)
                emit(f"CH{channel} Output Off command sent")
            except BaseException as error:
                off_error = error
                emit(f"WARNING: CH{channel} Output Off command failed: {error}")
            try:
                off_confirmed = not driver.read_output_state(channel)
                emit(
                    f"CH{channel} physical OFF confirmation: "
                    f"{'confirmed' if off_confirmed else 'FAILED (still ON)'}"
                )
            except BaseException as error:
                off_confirm_error = error
                emit(f"WARNING: CH{channel} physical OFF could not be confirmed: {error}")
    finally:
        elapsed = max(0.0, monotonic() - started)
        emit(f"CH{channel} elapsed Output-On monitoring time: {elapsed:.3f} s")

    if not off_confirmed:
        cause = off_confirm_error or off_error or run_error
        raise MonitoredOutputSafetyError(
            f"CH{channel} physical OFF could not be confirmed after Output On was attempted."
        ) from cause
    try:
        final_states = {
            1: driver.read_output_state(1),
            2: driver.read_output_state(2),
        }
    except BaseException as error:
        raise MonitoredOutputSafetyError(
            "Final verification could not confirm both outputs OFF."
        ) from error
    if any(final_states.values()):
        raise MonitoredOutputSafetyError("Final verification did not confirm both outputs OFF.")
    emit("Final output verification: OP1=OFF; OP2=OFF")
    if off_error is not None:
        raise MonitoredOutputSafetyError(
            f"CH{channel} Output Off command failed although OFF was subsequently confirmed."
        ) from off_error
    if run_error is not None:
        raise run_error
    for item in (1, 2):
        _emit_channel_state(driver, item, emit)
    emit(f"Status byte: {driver.read_status_byte()}")


def run_check(
    args: argparse.Namespace,
    *,
    driver_factory: Callable[[str, float], QPX600DPDriver] = _make_driver,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    timestamp: Callable[[], str] = _utc_timestamp,
    emit: Callable[[str], None] = print,
    report_directory: Path | None = None,
) -> None:
    """Run after validating authorization; default path performs queries only."""
    validate_authorization(args)
    driver = driver_factory(args.port, args.timeout)
    driver.connect()
    try:
        if args.capture_status_once is not None:
            _run_status_forensics(
                driver,
                emit=emit,
                timestamp=timestamp,
                report_directory=report_directory or default_paths().log_directory,
            )
            return
        if args.monitor_output_for is not None:
            _run_monitored_output(
                args,
                driver,
                sleep=sleep,
                monotonic=monotonic,
                timestamp=timestamp,
                emit=emit,
            )
            return
        emit(f"Identification: {driver.identify()}")
        channel = args.channel
        if args.set_voltage is not None:
            driver.set_voltage(channel, args.set_voltage)
        if args.set_current is not None:
            driver.set_current(channel, args.set_current)
        if args.output_off:
            driver.output_off(channel)
        if args.output_on_for is not None:
            driver.output_on(channel)
            try:
                emit(f"Channel {channel} output enabled for {args.output_on_for:g} s")
                sleep(args.output_on_for)
            finally:
                driver.output_off(channel)
                emit(f"Channel {channel} OFF command sent")

        channels = (channel,) if channel is not None else (1, 2)
        for item in channels:
            _emit_channel_state(driver, item, emit)
        emit(f"Status byte: {driver.read_status_byte()}")
    finally:
        driver.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_check(args)
    except HardwareCheckAuthorizationError as error:
        parser.error(str(error))
    except Exception as error:
        print(f"Hardware check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
