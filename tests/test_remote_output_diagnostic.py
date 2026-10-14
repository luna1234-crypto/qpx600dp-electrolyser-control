"""Offline fake-serial tests for the standalone remote-output diagnostic."""

from __future__ import annotations

from argparse import Namespace
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qpx_electrolyser.instrument.hardware_check import (
    MONITORED_OUTPUT_CONFIRMATION,
    WRITE_CONFIRMATION,
)
from qpx_electrolyser.instrument.qpx600dp import QPX600DPDriver
from qpx_electrolyser.instrument.remote_output_diagnostic import (
    PAUSE_RESUME_MODE_CONFIRMATION,
    PAUSE_RESUME_OUTPUT_CONFIRMATION,
    RemoteOutputAuthorizationError,
    RemoteOutputDiagnosticError,
    run_diagnostic,
    run_pause_resume_diagnostic,
)
from qpx_electrolyser.instrument.serial_transport import SerialTransport


class ScriptedSerial:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = deque(responses)
        self.writes: list[bytes] = []
        self.is_open = True
        self.close_calls = 0

    def open_with(self, **_settings: object) -> "ScriptedSerial":
        return self

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        return self.responses.popleft() if self.responses else b""

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


def args(tmp_path: Path, **overrides: object) -> Namespace:
    values: dict[str, object] = {
        "port": "COM11",
        "timeout": 1.0,
        "confirm_write": WRITE_CONFIRMATION,
        "confirm_output_on": MONITORED_OUTPUT_CONFIRMATION,
        "pause_resume_diagnostic": None,
        "confirm_pause_resume_output_on": None,
        "report_dir": tmp_path,
    }
    values.update(overrides)
    return Namespace(**values)


def pause_resume_args(tmp_path: Path, **overrides: object) -> Namespace:
    values: dict[str, object] = {
        "confirm_output_on": None,
        "pause_resume_diagnostic": PAUSE_RESUME_MODE_CONFIRMATION,
        "confirm_pause_resume_output_on": PAUSE_RESUME_OUTPUT_CONFIRMATION,
    }
    values.update(overrides)
    return args(tmp_path, **values)


def successful_responses(*, monitor_states: tuple[bool, ...] = (True,) * 10) -> list[bytes]:
    responses = [
        b"THURLBY THANDAR,QPX600DP,REDACT-ME,1.20\r\n",
        b"0\r\n", b"0\r\n",  # both outputs initially OFF
        b"2\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",  # initial CH1
        b"2\r\n",  # range query performed by set_voltage
        b"V1 4.000\r\n", b"I1 0.080\r\n",  # write readback
        b"1\r\n", b"1\r\n", b"1\r\n",  # *OPC?, stable ON x2
    ]
    for state in monitor_states:
        responses.append(b"1\r\n" if state else b"0\r\n")
        if state:
            responses.extend((b"3.990V\r\n", b"0.040A\r\n"))
    responses.extend([
        b"1\r\n", b"0\r\n", b"0\r\n",  # cleanup *OPC?, stable OFF x2
        b"2\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",
        b"0.000V\r\n", b"0.000A\r\n", b"0\r\n",
    ])
    return responses


def harness(
    tmp_path: Path, responses: list[bytes]
) -> tuple[ScriptedSerial, Callable[[Namespace], object], list[float]]:
    fake = ScriptedSerial(responses)
    now = [100.0]

    def factory(port: str, timeout: float) -> QPX600DPDriver:
        transport = SerialTransport(
            port,
            timeout_s=timeout,
            write_timeout_s=timeout,
            serial_factory=fake.open_with,
        )
        return QPX600DPDriver(transport)

    def sleep(delay: float) -> None:
        now[0] += delay

    def run(arguments: Namespace):  # type: ignore[no-untyped-def]
        return run_diagnostic(
            arguments,
            driver_factory=factory,
            monotonic=lambda: now[0],
            sleep=sleep,
            utc_now=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
            emit=lambda _line: None,
        )

    return fake, run, now


def pause_resume_harness(
    tmp_path: Path, responses: list[bytes]
) -> tuple[ScriptedSerial, Callable[[Namespace], object], list[float]]:
    fake = ScriptedSerial(responses)
    now = [100.0]

    def factory(port: str, timeout: float) -> QPX600DPDriver:
        transport = SerialTransport(
            port,
            timeout_s=timeout,
            write_timeout_s=timeout,
            serial_factory=fake.open_with,
        )
        return QPX600DPDriver(transport)

    def sleep(delay: float) -> None:
        now[0] += delay

    def run(arguments: Namespace):  # type: ignore[no-untyped-def]
        return run_pause_resume_diagnostic(
            arguments,
            driver_factory=factory,
            monotonic=lambda: now[0],
            sleep=sleep,
            utc_now=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
            emit=lambda _line: None,
        )

    return fake, run, now


def channel_readback_responses(*, output_on: bool = False) -> list[bytes]:
    return [
        b"2\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",
        b"0.000V\r\n", b"0.000A\r\n",
        b"1\r\n" if output_on else b"0\r\n",
    ]


def pause_resume_successful_responses() -> list[bytes]:
    responses = [
        b"THURLBY THANDAR,QPX600DP,REDACT-ME,1.20\r\n",
        b"0\r\n", b"0\r\n",
        b"2\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",
        b"2\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",
        b"1\r\n", b"1\r\n", b"1\r\n",
    ]
    responses.extend([b"1\r\n", b"3.990V\r\n", b"0.040A\r\n"] * 3)
    responses.extend([b"1\r\n", b"0\r\n", b"0\r\n"])
    responses.extend(channel_readback_responses())
    responses.extend([b"0\r\n", b"0\r\n"])
    responses.extend([b"0\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n"])
    responses.extend([b"1\r\n", b"1\r\n", b"1\r\n"])
    responses.extend([b"1\r\n", b"3.991V\r\n", b"0.039A\r\n"] * 5)
    responses.extend([b"1\r\n", b"0\r\n", b"0\r\n"])
    responses.extend(channel_readback_responses())
    return responses


def mutation_writes(fake: ScriptedSerial) -> list[bytes]:
    return [item for item in fake.writes if not item.rstrip(b"\n").endswith(b"?")]


def test_success_has_fixed_ten_samples_raw_report_and_exactly_one_on_off(
    tmp_path: Path,
) -> None:
    fake, run, now = harness(tmp_path, successful_responses())
    result = run(args(tmp_path))

    assert len(result.samples) == 10
    assert [sample.elapsed_s for sample in result.samples] == pytest.approx(range(1, 11))
    assert result.final_readback.output_enabled is False
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert mutation_writes(fake) == [
        b"V1 4.000\n", b"I1 0.08\n", b"OP1 1\n", b"OP1 0\n",
    ]
    assert fake.writes == [
        b"*IDN?\n", b"OP1?\n", b"OP2?\n",
        b"VRANGE1?\n", b"V1?\n", b"I1?\n",
        b"VRANGE1?\n", b"V1 4.000\n", b"I1 0.08\n",
        b"V1?\n", b"I1?\n",
        b"OP1 1\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
        *([b"OP1?\n", b"V1O?\n", b"I1O?\n"] * 10),
        b"OP1 0\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
        b"VRANGE1?\n", b"V1?\n", b"I1?\n",
        b"V1O?\n", b"I1O?\n", b"OP1?\n",
    ]
    assert now[0] == pytest.approx(110.1)
    text = result.report_path.read_text(encoding="utf-8")
    assert '"command": "OP1 1"' in text
    assert '"raw_response": "b\'3.990V' in text
    assert "\\\\r\\\\n" in text
    assert "REDACT-ME" not in text
    assert "sample 10: elapsed=10.000 s" in text
    assert fake.close_calls == 1


def test_spontaneous_off_stops_without_measurement_then_cleans_up_once(
    tmp_path: Path,
) -> None:
    states = (True, True, False)
    fake, run, _now = harness(tmp_path, successful_responses(monitor_states=states))
    with pytest.raises(RemoteOutputDiagnosticError, match="reported OFF at 3.000 s"):
        run(args(tmp_path))
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.writes.count(b"V1O?\n") == 3  # two monitor reads plus final readback
    assert fake.close_calls == 1


def test_timeout_closes_without_retry_and_reports_unconfirmed_off(tmp_path: Path) -> None:
    responses = successful_responses()
    responses[12] = b""  # first monitored OP1? times out and invalidates framing
    fake, run, _now = harness(tmp_path, responses[:13])
    with pytest.raises(RemoteOutputDiagnosticError, match="physical OFF is unconfirmed"):
        run(args(tmp_path))
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 0  # invalidated transport rejects the one attempt
    assert fake.writes.count(b"OP1?\n") == 4
    assert fake.close_calls == 1


def test_identity_mismatch_stops_before_any_state_or_mutation(tmp_path: Path) -> None:
    fake, run, _now = harness(
        tmp_path,
        [b"THURLBY THANDAR,CPX400DP,REDACT-ME,1.20\r\n"],
    )
    with pytest.raises(RemoteOutputDiagnosticError, match="not QPX600DP"):
        run(args(tmp_path))
    assert fake.writes == [b"*IDN?\n"]
    assert fake.close_calls == 1


def test_wrong_range_stops_before_any_setting_or_output_write(tmp_path: Path) -> None:
    fake, run, _now = harness(tmp_path, [
        b"THURLBY THANDAR,QPX600DP,REDACT-ME,1.20\r\n",
        b"0\r\n", b"0\r\n",
        b"1\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",
    ])
    with pytest.raises(RemoteOutputDiagnosticError, match="must already be in 80 V"):
        run(args(tmp_path))
    assert mutation_writes(fake) == []
    assert fake.close_calls == 1


@pytest.mark.parametrize(("op1", "op2"), [(True, False), (False, True), (True, True)])
def test_initially_on_rejection_queries_both_outputs_and_never_mutates(
    tmp_path: Path, op1: bool, op2: bool,
) -> None:
    fake, run, _now = harness(tmp_path, [
        b"THURLBY THANDAR,QPX600DP,REDACT-ME,1.20\r\n",
        b"1\r\n" if op1 else b"0\r\n",
        b"1\r\n" if op2 else b"0\r\n",
    ])
    with pytest.raises(RemoteOutputDiagnosticError, match="Both outputs must be OFF"):
        run(args(tmp_path))
    assert fake.writes == [b"*IDN?\n", b"OP1?\n", b"OP2?\n"]
    assert mutation_writes(fake) == []
    assert fake.close_calls == 1


def test_wrong_confirmation_is_rejected_before_backend_or_report(tmp_path: Path) -> None:
    created = False

    def factory(_port: str, _timeout: float) -> QPX600DPDriver:
        nonlocal created
        created = True
        raise AssertionError("must not create backend")

    with pytest.raises(RemoteOutputAuthorizationError, match="confirm-write"):
        run_diagnostic(
            args(tmp_path, confirm_write="wrong"), driver_factory=factory
        )
    assert not created
    assert not list(tmp_path.iterdir())


def test_pause_resume_full_success_uses_two_exact_output_lifetimes(
    tmp_path: Path,
) -> None:
    fake, run, now = pause_resume_harness(
        tmp_path, pause_resume_successful_responses()
    )
    result = run(pause_resume_args(tmp_path))

    assert len(result.samples) == 8
    assert [sample.elapsed_s for sample in result.samples[:3]] == pytest.approx([1, 2, 3])
    assert [sample.elapsed_s for sample in result.samples[3:]] == pytest.approx([1, 2, 3, 4, 5])
    assert result.final_readback.output_enabled is False
    assert fake.writes.count(b"OP1 1\n") == 2
    assert fake.writes.count(b"OP1 0\n") == 2
    assert fake.writes.count(b"*OPC?\n") == 4
    assert mutation_writes(fake) == [
        b"V1 4.000\n", b"I1 0.08\n",
        b"OP1 1\n", b"OP1 0\n", b"OP1 1\n", b"OP1 0\n",
    ]
    assert now[0] == pytest.approx(110.2)
    assert result.report_path.name.startswith("qpx600dp_pause_resume_")
    assert fake.close_calls == 1


def test_pause_off_failure_never_repeats_pause_write(tmp_path: Path) -> None:
    responses = pause_resume_successful_responses()
    responses[29] = b"1\r\n"
    fake, run, _now = pause_resume_harness(tmp_path, responses[:30])

    with pytest.raises(RemoteOutputDiagnosticError, match="physical OFF is unconfirmed"):
        run(pause_resume_args(tmp_path))
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.close_calls == 1


def test_resume_on_then_off_fails_and_performs_one_resume_cleanup(tmp_path: Path) -> None:
    responses = pause_resume_successful_responses()
    responses[37] = b"0\r\n"
    responses = responses[:38] + [b"1\r\n", b"0\r\n", b"0\r\n"] + channel_readback_responses()
    fake, run, _now = pause_resume_harness(tmp_path, responses)

    with pytest.raises(RemoteOutputDiagnosticError, match="did not remain confirmed"):
        run(pause_resume_args(tmp_path))
    assert fake.writes.count(b"OP1 1\n") == 2
    assert fake.writes.count(b"OP1 0\n") == 2
    assert fake.close_calls == 1


def test_resume_setpoint_mismatch_stops_while_already_off(tmp_path: Path) -> None:
    responses = pause_resume_successful_responses()
    responses[33] = b"V1 3.900\r\n"
    fake, run, _now = pause_resume_harness(tmp_path, responses[:35])

    with pytest.raises(RemoteOutputDiagnosticError, match="voltage setpoint changed"):
        run(pause_resume_args(tmp_path))
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.close_calls == 1


def test_initial_phase_failure_gets_exactly_one_failure_cleanup(tmp_path: Path) -> None:
    responses = pause_resume_successful_responses()
    responses[18] = b"0\r\n"
    responses = responses[:19] + [b"1\r\n", b"0\r\n", b"0\r\n"] + channel_readback_responses()
    fake, run, _now = pause_resume_harness(tmp_path, responses)

    with pytest.raises(RemoteOutputDiagnosticError, match="initial at 3.000 s"):
        run(pause_resume_args(tmp_path))
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.close_calls == 1


def test_pause_resume_tokens_are_rejected_before_backend(tmp_path: Path) -> None:
    created = False

    def factory(_port: str, _timeout: float) -> QPX600DPDriver:
        nonlocal created
        created = True
        raise AssertionError("must not create backend")

    with pytest.raises(RemoteOutputAuthorizationError, match="Pause/Resume Output On"):
        run_pause_resume_diagnostic(
            pause_resume_args(
                tmp_path,
                confirm_pause_resume_output_on="wrong",
            ),
            driver_factory=factory,
        )
    assert not created
    assert not list(tmp_path.iterdir())
