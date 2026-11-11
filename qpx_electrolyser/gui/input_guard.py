"""Prevent accidental mouse-wheel edits while preserving panel scrolling."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QComboBox, QWidget


class WheelValueGuard(QObject):
    """Redirect wheel events on closed selectors/numeric fields to their page."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel:
            return False
        if isinstance(watched, QComboBox):
            if watched.view().isVisible():
                return False
        elif not isinstance(watched, QAbstractSpinBox):
            return False
        self._scroll_ancestor(watched, event)
        event.accept()
        return True

    @staticmethod
    def _scroll_ancestor(widget: QWidget, event: QEvent) -> None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                bar = parent.verticalScrollBar()
                delta = event.angleDelta().y()  # type: ignore[attr-defined]
                if delta:
                    steps = delta / 120.0
                    bar.setValue(round(bar.value() - steps * max(20, bar.singleStep() * 3)))
                return
            parent = parent.parentWidget()


def protect_wheel_value_widgets(root: QWidget, guard: WheelValueGuard) -> None:
    """Install one guard on all existing selectors and numeric spin boxes."""
    for widget_type in (QComboBox, QAbstractSpinBox):
        for widget in root.findChildren(widget_type):
            widget.installEventFilter(guard)
