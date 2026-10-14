"""One-shot CH1 diagnostic implemented directly on pyserial.

This module intentionally imports no project transport, driver, GUI, queue,
controller, logger or plotting code. It never enumerates serial ports.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import sys
import time
from typing import Any


WRITE_CONFIRMATION = "WRITE-QPX600DP"
OUTPUT_ON_CONFIRMATION = "MONITOR-QPX600DP-OUTPUT"
VOLTAGE_V = 4.000
CURRENT_A = 0.080
RUN_DURATION_S = 10.0
SAMPLE_INTERVAL_S = 1.0
CONFIRMATION_INTERVAL_S = 0.05
MAX_FRAME_BYTES = 512
_FIXED_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


class DirectDiagnosticError(RuntimeError):
    def __init__(self, message: str, report_path: Path | None = None) -> None:
        super().__init__(message)
        self.report_path = report_path


class DirectAuthorizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DirectSample:
    elapsed_s: float
    measured_voltage_v: float
    measured_current_a: float


@dataclass(frozen=True, slots=True)
class DirectResult:
    report_path: Path
    samples: tuple[DirectSample, ...]
    final_output_off: bool


class _RawByteReport:
    def __init__(self, directory: Path, now: datetime) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = directory / f"qpx600dp_direct_pyserial_{stamp}.log"
        self._stream = self.path.open("x", encoding="utf-8", newline="\n")

    def _write(self, kind: str, detail: str) -> None:
        wall = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self._stream.write(
            f"{wall} monotonic={time.monotonic():.9f} {kind} {detail}\n"
        )
        self._stream.flush()

    def tx(self, frame: bytes) -> None:
        self._write("TX", repr(frame))

    def rx(self, frame: bytes) -> None:
        self._write("RX", repr(frame))

    def note(self, message: str) -> None:
        self._write("NOTE", message)

    def close(self) -> None:
        self._stream.close()


def _open_pyserial(**settings: object) -> Any:
    import serial

    return serial.Serial(**settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot direct-pyserial QPX600DP CH1 diagnostic; no port "
            "enumeration or application instrument layers."
        )
    )
    parser.add_argument("--port", required=True, help="Explicit serial port name")
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--confirm-write", required=True, metavar=WRITE_CONFIRMATION)
    parser.add_argument(
        "--confirm-output-on", required=True, metavar=OUTPUT_ON_CONFIRMATION
    )
    parser.add_argument(
        "--report-dir", type=Path, default=Path.cwd() / "logs"
    )
    return parser


def validate_authorization(args: argparse.Namespace) -> None:
    if not isinstance(args.port, str) or not args.port.strip():
        raise DirectAuthorizationError("--port must be an explicit nonempty name.")
    if not isinstance(args.timeout, (int, float)) or isinstance(args.timeout, bool):
        raise DirectAuthorizationError("--timeout must be finite and positive.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise DirectAuthorizationError("--timeout must be finite and positive.")
    if args.confirm_write != WRITE_CONFIRMATION:
        raise DirectAuthorizationError(
            f"Writes require --confirm-write {WRITE_CONFIRMATION}."
        )
    if args.confirm_output_on != OUTPUT_ON_CONFIRMATION:
        raise DirectAuthorizationError(
            "Output On requires --confirm-output-on " + OUTPUT_ON_CONFIRMATION + "."
        )
    if not isinstance(args.report_dir, Path):
        raise DirectAuthorizationError("--report-dir must be a filesystem path.")


class _DirectSession:
    def __init__(self, serial_port: Any, report: _RawByteReport) -> None:
        self.serial = serial_port
        self.report = report
        self.framing_aligned = True

    def command(self, text: str) -> None:
        frame = _encode_command(text)
        self.report.tx(frame)
        written = self.serial.write(frame)
        if written != len(frame):
            raise DirectDiagnosticError(
                f"Partial serial write: {written}/{len(frame)} bytes."
            )
        self.serial.flush()

    def query(self, text: str) -> str:
        if not self.framing_aligned:
            raise DirectDiagnosticError(
                "Serial response framing is no longer aligned; refusing query."
            )
        self.command(text)
        try:
            frame = self.serial.read_until(
                expected=b"\r\n", size=MAX_FRAME_BYTES + 2
            )
        except BaseException:
            self.framing_aligned = False
            raise
        self.report.rx(frame)
        if not frame.endswith(b"\r\n"):
            self.framing_aligned = False
            raise DirectDiagnosticError(
                "Timed out before a complete CRLF-terminated response arrived."
            )
        payload = frame[:-2]
        if len(payload) > MAX_FRAME_BYTES or b"\r" in payload or b"\n" in payload:
            self.framing_aligned = False
            raise DirectDiagnosticError("Instrument response framing is invalid.")
        try:
            return payload.decode("ascii")
        except UnicodeDecodeError as error:
            self.framing_aligned = False
            raise DirectDiagnosticError("Instrument response is not ASCII.") from error


def _encode_command(text: str) -> bytes:
    if not text or any(character in text for character in "\r\n;"):
        raise DirectDiagnosticError("Refused invalid or compound serial command.")
    try:
        encoded = text.encode("ascii")
    except UnicodeEncodeError as error:
        raise DirectDiagnosticError("Command must be printable ASCII.") from error
    if any(byte < 0x20 or byte > 0x7E for byte in encoded):
        raise DirectDiagnosticError("Command must be printable ASCII.")
    return encoded + b"\n"


def _parse_identity(response: str) -> None:
    parts = [part.strip() for part in response.split(",")]
    if len(parts) != 4 or any(not part for part in parts):
        raise DirectDiagnosticError("Malformed *IDN? response.")
    if parts[1].upper() != "QPX600DP":
        raise DirectDiagnosticError("Connected instrument is not QPX600DP.")


def _parse_output(response: str) -> bool:
    value = response.strip()
    if value not in {"0", "1"}:
        raise DirectDiagnosticError("Malformed output-state response.")
    return value == "1"


def _parse_prefixed(response: str, prefix: str) -> float:
    match = re.fullmatch(rf"{re.escape(prefix)}\s+({_FIXED_NUMBER.pattern})", response.strip())
    if match is None:
        raise DirectDiagnosticError(f"Malformed {prefix} setpoint response.")
    return _parse_nonnegative(match.group(1))


def _parse_suffixed(response: str, suffix: str) -> float:
    match = re.fullmatch(rf"({_FIXED_NUMBER.pattern}){re.escape(suffix)}", response.strip())
    if match is None:
        raise DirectDiagnosticError(f"Malformed measured-{suffix} response.")
    return _parse_nonnegative(match.group(1))


def _parse_nonnegative(text: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise DirectDiagnosticError("Response is not numeric.") from error
    if not math.isfinite(value) or value < 0:
        raise DirectDiagnosticError("Response must be finite and nonnegative.")
    return value


def _require_opc(session: _DirectSession) -> None:
    if session.query("*OPC?").strip() != "1":
        raise DirectDiagnosticError("Malformed *OPC? response; expected 1.")


def _confirm_stable_state(
    session: _DirectSession,
    expected_on: bool,
    *,
    sleep: Callable[[float], None],
) -> None:
    _require_opc(session)
    first = _parse_output(session.query("OP1?"))
    if first is not expected_on:
        raise DirectDiagnosticError(
            f"CH1 did not report {'ON' if expected_on else 'OFF'}."
        )
    sleep(CONFIRMATION_INTERVAL_S)
    second = _parse_output(session.query("OP1?"))
    if second is not expected_on:
        raise DirectDiagnosticError(
            f"CH1 did not remain {'ON' if expected_on else 'OFF'}."
        )


def _verify_and_program_settings(session: _DirectSession) -> None:
    range_response = session.query("VRANGE1?").strip()
    if range_response != "2":
        raise DirectDiagnosticError("CH1 must be in the 80 V range (VRANGE1? = 2).")
    session.command("V1 4.000")
    session.command("I1 0.08")
    voltage = _parse_prefixed(session.query("V1?"), "V1")
    current = _parse_prefixed(session.query("I1?"), "I1")
    if not math.isclose(voltage, VOLTAGE_V, rel_tol=0.0, abs_tol=0.005):
        raise DirectDiagnosticError("CH1 voltage readback did not equal 4.000 V.")
    if not math.isclose(current, CURRENT_A, rel_tol=0.0, abs_tol=0.005):
        raise DirectDiagnosticError("CH1 current readback did not equal 0.080 A.")


def run_diagnostic(
    args: argparse.Namespace,
    *,
    serial_factory: Callable[..., Any] = _open_pyserial,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    emit: Callable[[str], None] = print,
) -> DirectResult:
    validate_authorization(args)
    report = _RawByteReport(args.report_dir, utc_now())
    serial_port: Any | None = None
    session: _DirectSession | None = None
    output_on_attempted = False
    output_off_attempted = False
    final_output_off = False
    samples: list[DirectSample] = []
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        report.note("direct-pyserial one-shot diagnostic started")
        serial_port = serial_factory(
            port=args.port.strip(),
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=float(args.timeout),
            write_timeout=float(args.timeout),
            xonxoff=True,
            rtscts=False,
            dsrdtr=False,
        )
        if not getattr(serial_port, "is_open", False):
            raise DirectDiagnosticError("The explicitly supplied serial port did not open.")
        session = _DirectSession(serial_port, report)
        _parse_identity(session.query("*IDN?"))
        report.note("QPX600DP identity accepted")
        op1 = _parse_output(session.query("OP1?"))
        op2 = _parse_output(session.query("OP2?"))
        if op1 or op2:
            raise DirectDiagnosticError(
                f"Both outputs must be OFF; observed OP1={int(op1)}, OP2={int(op2)}."
            )
        report.note("preflight confirmed OP1=0 and OP2=0")
        _verify_and_program_settings(session)
        report.note("CH1 fixed settings verified: 4.000 V / 0.080 A / 80 V range")

        output_on_attempted = True
        session.command("OP1 1")
        report.note("OP1 1 transmitted exactly once")
        _confirm_stable_state(session, True, sleep=sleep)
        report.note("CH1 stable ON confirmed by *OPC? and two OP1? responses")

        started_at = monotonic()
        for index in range(1, int(RUN_DURATION_S / SAMPLE_INTERVAL_S) + 1):
            delay = started_at + index * SAMPLE_INTERVAL_S - monotonic()
            if delay > 0:
                sleep(delay)
            elapsed = monotonic() - started_at
            if not _parse_output(session.query("OP1?")):
                raise DirectDiagnosticError(
                    f"CH1 unexpectedly reported OFF at {elapsed:.3f} s."
                )
            voltage = _parse_suffixed(session.query("V1O?"), "V")
            current = _parse_suffixed(session.query("I1O?"), "A")
            sample = DirectSample(elapsed, voltage, current)
            samples.append(sample)
            line = (
                f"sample {index:02d}: elapsed={elapsed:.3f} s; OP1=ON; "
                f"V1O={voltage:.6g} V; I1O={current:.6g} A"
            )
            report.note(line)
            emit(line)
    except BaseException as error:
        primary_error = error
        report.note(f"run stopped: {str(error) or type(error).__name__}")
    finally:
        if session is not None and output_on_attempted and not output_off_attempted:
            output_off_attempted = True
            try:
                session.command("OP1 0")
                report.note("OP1 0 transmitted exactly once for guaranteed cleanup")
                _confirm_stable_state(session, False, sleep=sleep)
                final_output_off = True
                report.note("CH1 stable OFF confirmed")
            except BaseException as error:
                cleanup_error = error
                report.note(
                    "cleanup failed; physical OFF is unconfirmed: "
                    f"{str(error) or type(error).__name__}"
                )
        if serial_port is not None:
            try:
                serial_port.close()
                report.note("serial port closed")
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                report.note(f"serial close failed: {str(error) or type(error).__name__}")
        report.note("direct-pyserial one-shot diagnostic finished; no retry attempted")
        report_path = report.path
        report.close()

    if cleanup_error is not None:
        prefix = (
            f"{str(primary_error) or type(primary_error).__name__}; "
            if primary_error is not None
            else ""
        )
        raise DirectDiagnosticError(
            prefix
            + "Output-Off cleanup failed; physical OFF is unconfirmed: "
            + (str(cleanup_error) or type(cleanup_error).__name__),
            report_path,
        ) from cleanup_error
    if primary_error is not None:
        raise DirectDiagnosticError(
            str(primary_error) or type(primary_error).__name__, report_path
        ) from primary_error
    if not final_output_off:
        raise DirectDiagnosticError(
            "Diagnostic ended without stable OFF confirmation.", report_path
        )
    emit(f"Report: {report_path}")
    return DirectResult(report_path, tuple(samples), True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_diagnostic(args)
    except (DirectAuthorizationError, DirectDiagnosticError) as error:
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
