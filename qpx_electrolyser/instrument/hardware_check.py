"""Explicit-port QPX600DP commissioning helper; read-only unless strongly authorised.

This module never enumerates serial ports. Run with
``python -m qpx_electrolyser.instrument.hardware_check --help``.
"""

import argparse
from collections.abc import Callable, Sequence
import sys
import time

from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.serial_transport import SerialTransport


WRITE_CONFIRMATION = "WRITE-QPX600DP"
OUTPUT_ON_CONFIRMATION = "ENABLE-QPX600DP-OUTPUT"


class HardwareCheckAuthorizationError(ValueError):
    """A requested hardware mutation lacks explicit command-line authorization."""


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
    return parser


def validate_authorization(args: argparse.Namespace) -> None:
    mutations = (
        args.set_voltage is not None
        or args.set_current is not None
        or args.output_off
        or args.output_on_for is not None
    )
    if mutations and args.confirm_write != WRITE_CONFIRMATION:
        raise HardwareCheckAuthorizationError(
            f"Any write requires --confirm-write {WRITE_CONFIRMATION}."
        )
    if mutations and args.channel not in (1, 2):
        raise HardwareCheckAuthorizationError("Any write requires --channel 1 or 2.")
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


def _make_driver(port: str, timeout_s: float) -> QPX600DPDriver:
    return QPX600DPDriver(SerialTransport(
        port,
        timeout_s=timeout_s,
        write_timeout_s=timeout_s,
    ))


def run_check(
    args: argparse.Namespace,
    *,
    driver_factory: Callable[[str, float], QPX600DPDriver] = _make_driver,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
) -> None:
    """Run after validating authorization; default path performs queries only."""
    validate_authorization(args)
    driver = driver_factory(args.port, args.timeout)
    driver.connect()
    try:
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
            voltage_range = driver.read_voltage_range(item)
            emit(
                f"CH{item}: range={voltage_range.name}; "
                f"set={driver.read_set_voltage(item):g} V/{driver.read_set_current(item):g} A; "
                f"output={'ON' if driver.read_output_state(item) else 'OFF'}; "
                f"measured={driver.read_voltage(item):g} V/{driver.read_current(item):g} A"
            )
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
