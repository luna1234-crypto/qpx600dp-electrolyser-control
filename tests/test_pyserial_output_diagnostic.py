"""Fake-pyserial coverage for the isolated direct-byte diagnostic."""

from __future__ import annotations

from argparse import Namespace
import ast
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import pytest

import qpx_electrolyser.instrument.pyserial_output_diagnostic as diagnostic


class FakeSerial:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = deque(responses)
        self.writes: list[bytes] = []
        self.settings: dict[str, object] = {}
        self.is_open = True
        self.close_calls = 0

    def open_with(self, **settings: object) -> "FakeSerial":
        self.settings = settings
        return self

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        assert expected == b"\r\n"
        assert size == 514
        return self.responses.popleft() if self.responses else b""

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


def args(tmp_path: Path, **overrides: object) -> Namespace:
    values: dict[str, object] = {
        "port": "COM11",
        "timeout": 1.0,
        "confirm_write": diagnostic.WRITE_CONFIRMATION,
        "confirm_output_on": diagnostic.OUTPUT_ON_CONFIRMATION,
        "report_dir": tmp_path,
    }
    values.update(overrides)
    return Namespace(**values)


def successful_responses(*, states: tuple[bool, ...] = (True,) * 10) -> list[bytes]:
    responses = [
        b"THURLBY THANDAR,QPX600DP,RAW-SERIAL,1.20\r\n",
        b"0\r\n", b"0\r\n",
        b"2\r\n", b"V1 4.000\r\n", b"I1 0.080\r\n",
        b"1\r\n", b"1\r\n", b"1\r\n",
    ]
    for state in states:
        responses.append(b"1\r\n" if state else b"0\r\n")
        if state:
            responses.extend([b"3.990V\r\n", b"0.040A\r\n"])
    responses.extend([b"1\r\n", b"0\r\n", b"0\r\n"])
    return responses


def harness(
    tmp_path: Path, responses: list[bytes]
) -> tuple[FakeSerial, object, list[float]]:
    fake = FakeSerial(responses)
    now = [100.0]

    def sleep(delay: float) -> None:
        now[0] += delay

    def run(arguments: Namespace):  # type: ignore[no-untyped-def]
        return diagnostic.run_diagnostic(
            arguments,
            serial_factory=fake.open_with,
            monotonic=lambda: now[0],
            sleep=sleep,
            utc_now=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
            emit=lambda _line: None,
        )

    return fake, run, now


def test_success_is_raw_pyserial_8n1_xonxoff_and_exactly_one_on_off(
    tmp_path: Path,
) -> None:
    fake, run, now = harness(tmp_path, successful_responses())
    result = run(args(tmp_path))  # type: ignore[operator]

    assert len(result.samples) == 10
    assert [sample.elapsed_s for sample in result.samples] == pytest.approx(range(1, 11))
    assert result.final_output_off is True
    assert fake.settings == {
        "port": "COM11",
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 1.0,
        "write_timeout": 1.0,
        "xonxoff": True,
        "rtscts": False,
        "dsrdtr": False,
    }
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.writes == [
        b"*IDN?\n", b"OP1?\n", b"OP2?\n", b"VRANGE1?\n",
        b"V1 4.000\n", b"I1 0.08\n", b"V1?\n", b"I1?\n",
        b"OP1 1\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
        *([b"OP1?\n", b"V1O?\n", b"I1O?\n"] * 10),
        b"OP1 0\n", b"*OPC?\n", b"OP1?\n", b"OP1?\n",
    ]
    assert now[0] == pytest.approx(110.1)
    report_text = result.report_path.read_text(encoding="utf-8")
    assert " TX b'OP1 1\\n'" in report_text
    assert " RX b'3.990V\\r\\n'" in report_text
    assert "monotonic=" in report_text
    assert fake.close_calls == 1


def test_spontaneous_off_stops_and_cleans_up_once(tmp_path: Path) -> None:
    fake, run, _now = harness(
        tmp_path, successful_responses(states=(True, True, False))
    )
    with pytest.raises(diagnostic.DirectDiagnosticError, match="OFF at 3.000 s"):
        run(args(tmp_path))  # type: ignore[operator]
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.close_calls == 1


def test_stable_on_failure_still_has_one_guaranteed_cleanup(tmp_path: Path) -> None:
    responses = successful_responses()
    responses[8] = b"0\r\n"
    responses = responses[:9] + [b"1\r\n", b"0\r\n", b"0\r\n"]
    fake, run, _now = harness(tmp_path, responses)

    with pytest.raises(diagnostic.DirectDiagnosticError, match="remain ON"):
        run(args(tmp_path))  # type: ignore[operator]
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.close_calls == 1


@pytest.mark.parametrize(("op1", "op2"), [(True, False), (False, True), (True, True)])
def test_initially_on_rejection_never_writes_settings_or_output(
    tmp_path: Path, op1: bool, op2: bool,
) -> None:
    fake, run, _now = harness(tmp_path, [
        b"THURLBY THANDAR,QPX600DP,RAW-SERIAL,1.20\r\n",
        b"1\r\n" if op1 else b"0\r\n",
        b"1\r\n" if op2 else b"0\r\n",
    ])
    with pytest.raises(diagnostic.DirectDiagnosticError, match="Both outputs must be OFF"):
        run(args(tmp_path))  # type: ignore[operator]
    assert fake.writes == [b"*IDN?\n", b"OP1?\n", b"OP2?\n"]
    assert fake.close_calls == 1


def test_identity_mismatch_stops_before_other_queries(tmp_path: Path) -> None:
    fake, run, _now = harness(
        tmp_path, [b"THURLBY THANDAR,CPX400DP,RAW-SERIAL,1.20\r\n"]
    )
    with pytest.raises(diagnostic.DirectDiagnosticError, match="not QPX600DP"):
        run(args(tmp_path))  # type: ignore[operator]
    assert fake.writes == [b"*IDN?\n"]
    assert fake.close_calls == 1


def test_timeout_after_output_on_attempts_off_once_and_reports_cleanup_failure(
    tmp_path: Path,
) -> None:
    responses = successful_responses()
    responses[9] = b""
    fake, run, _now = harness(tmp_path, responses[:10])
    with pytest.raises(diagnostic.DirectDiagnosticError, match="physical OFF is unconfirmed"):
        run(args(tmp_path))  # type: ignore[operator]
    assert fake.writes.count(b"OP1 1\n") == 1
    assert fake.writes.count(b"OP1 0\n") == 1
    assert fake.writes.count(b"*OPC?\n") == 1  # no query on misaligned framing
    assert fake.close_calls == 1


def test_bad_confirmation_rejected_before_report_or_serial_open(tmp_path: Path) -> None:
    opened = False

    def factory(**_settings: object) -> FakeSerial:
        nonlocal opened
        opened = True
        raise AssertionError("must not open")

    with pytest.raises(diagnostic.DirectAuthorizationError, match="confirm-output-on"):
        diagnostic.run_diagnostic(
            args(tmp_path, confirm_output_on="wrong"), serial_factory=factory
        )
    assert opened is False
    assert not list(tmp_path.iterdir())


def test_module_imports_no_project_or_prohibited_framework_layers() -> None:
    source = Path(diagnostic.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith("qpx_electrolyser") for name in imported)
    assert not any(name.startswith("PySide") for name in imported)
    assert "threading" not in imported
