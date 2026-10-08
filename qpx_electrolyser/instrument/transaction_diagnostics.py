"""Thread-local metadata for real-hardware serial transaction diagnostics."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TransactionDiagnosticContext:
    session_id: int = 0
    request_id: int = 0
    operation_name: str = "unscoped"
    priority: str = "unscoped"
    output_off_reason: str = ""


_CURRENT_CONTEXT: ContextVar[TransactionDiagnosticContext] = ContextVar(
    "qpx_hardware_transaction_context",
    default=TransactionDiagnosticContext(),
)


def current_transaction_context() -> TransactionDiagnosticContext:
    return _CURRENT_CONTEXT.get()


@contextmanager
def transaction_diagnostic_context(
    *,
    session_id: int,
    request_id: int,
    operation_name: str,
    priority: str,
    output_off_reason: str = "",
) -> Iterator[None]:
    token = _CURRENT_CONTEXT.set(TransactionDiagnosticContext(
        session_id=session_id,
        request_id=request_id,
        operation_name=operation_name,
        priority=priority,
        output_off_reason=output_off_reason,
    ))
    try:
        yield
    finally:
        _CURRENT_CONTEXT.reset(token)
