"""Display-only mouse interaction shared by live experiment plots."""

from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QRubberBand, QToolTip, QWidget


@dataclass(frozen=True, slots=True)
class DisplayPoint:
    position: QPointF
    tooltip: str


class InteractivePlotCanvas(QWidget):
    """Provide bounded display zoom and nearest-point tooltips without data access."""

    POINT_TOLERANCE_PX = 12.0
    MIN_ZOOM_DRAG_PX = 6

    def __init__(self) -> None:
        super().__init__()
        self._plot_area = QRectF()
        self._display_points: tuple[DisplayPoint, ...] = ()
        self._zoom_view = (0.0, 1.0, 0.0, 1.0)
        self._drag_origin = None
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._last_tooltip_text = ""
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

    @property
    def zoom_view(self) -> tuple[float, float, float, float]:
        return self._zoom_view

    @property
    def last_tooltip_text(self) -> str:
        return self._last_tooltip_text

    def reset_zoom(self) -> None:
        self._zoom_view = (0.0, 1.0, 0.0, 1.0)
        self.update()

    def visible_bounds(
        self, x_min: float, x_max: float, y_min: float, y_max: float
    ) -> tuple[float, float, float, float]:
        x0, x1, y0, y1 = self._zoom_view
        x_span = max(x_max - x_min, 1e-12)
        y_span = max(y_max - y_min, 1e-12)
        return (
            x_min + x0 * x_span,
            x_min + x1 * x_span,
            y_min + y0 * y_span,
            y_min + y1 * y_span,
        )

    def set_interaction_geometry(
        self, area: QRectF, points: list[DisplayPoint]
    ) -> None:
        self._plot_area = QRectF(area)
        self._display_points = tuple(
            point for point in points if area.adjusted(-1, -1, 1, 1).contains(point.position)
        )

    def draw_interaction_overlay(self, painter: QPainter) -> None:
        if self._zoom_view == (0.0, 1.0, 0.0, 1.0):
            return
        painter.setPen(QPen(Qt.GlobalColor.darkBlue, 1, Qt.PenStyle.DashLine))
        painter.drawText(
            self._plot_area.adjusted(5, 4, -5, -4),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            "Zoomed — use Auto Range / Reset Zoom",
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        position = event.position()
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._plot_area.contains(position)
        ):
            self._drag_origin = position.toPoint()
            self._rubber_band.setGeometry(QRect(self._drag_origin, self._drag_origin))
            self._rubber_band.show()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._show_nearest_tooltip(position, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._drag_origin is not None:
            self._rubber_band.setGeometry(
                QRect(self._drag_origin, event.position().toPoint()).normalized()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.RightButton and self._drag_origin is not None:
            rectangle = QRectF(
                QRect(self._drag_origin, event.position().toPoint()).normalized()
            ).intersected(self._plot_area)
            self._drag_origin = None
            self._rubber_band.hide()
            if (
                rectangle.width() >= self.MIN_ZOOM_DRAG_PX
                and rectangle.height() >= self.MIN_ZOOM_DRAG_PX
            ):
                self._apply_zoom_rectangle(rectangle)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()

    def _apply_zoom_rectangle(self, rectangle: QRectF) -> None:
        area = self._plot_area
        if area.width() <= 0 or area.height() <= 0:
            return
        old_x0, old_x1, old_y0, old_y1 = self._zoom_view
        x_span = old_x1 - old_x0
        y_span = old_y1 - old_y0
        left_fraction = (rectangle.left() - area.left()) / area.width()
        right_fraction = (rectangle.right() - area.left()) / area.width()
        bottom_fraction = (area.bottom() - rectangle.bottom()) / area.height()
        top_fraction = (area.bottom() - rectangle.top()) / area.height()
        candidate = (
            old_x0 + max(0.0, min(1.0, left_fraction)) * x_span,
            old_x0 + max(0.0, min(1.0, right_fraction)) * x_span,
            old_y0 + max(0.0, min(1.0, bottom_fraction)) * y_span,
            old_y0 + max(0.0, min(1.0, top_fraction)) * y_span,
        )
        if candidate[1] > candidate[0] and candidate[3] > candidate[2]:
            self._zoom_view = candidate
            self.update()

    def _show_nearest_tooltip(self, position: QPointF, global_position) -> None:
        nearest: DisplayPoint | None = None
        nearest_distance = math.inf
        for point in self._display_points:
            dx = point.position.x() - position.x()
            dy = point.position.y() - position.y()
            distance = math.hypot(dx, dy)
            if distance < nearest_distance:
                nearest = point
                nearest_distance = distance
        if nearest is None or nearest_distance > self.POINT_TOLERANCE_PX:
            self._last_tooltip_text = ""
            QToolTip.hideText()
            return
        self._last_tooltip_text = nearest.tooltip
        QToolTip.showText(global_position, nearest.tooltip, self)
