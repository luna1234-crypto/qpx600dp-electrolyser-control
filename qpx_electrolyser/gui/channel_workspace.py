"""Reusable compact channel workspace and collapsible plot containers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class ChannelWorkspace(QGroupBox):
    """Keep one channel's status visible while switching protocol editors."""

    TAB_NAMES = (
        "Manual", "Chronopotentiometry (CP)", "Chronoamperometry (CA)", "Voltage Scan", "Current Scan",
        "Polarisation", "Custom Sequence",
    )

    def __init__(self, channel: int, panels: tuple[QWidget, ...]) -> None:
        super().__init__(f"Channel {channel}")
        self.channel = channel
        self.setObjectName(f"channelWorkspace{channel}")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        status = QWidget()
        status.setObjectName(f"channelCompactStatus{channel}")
        status_layout = QGridLayout(status)
        status_layout.setContentsMargins(6, 4, 6, 4)
        self.output_value = QLabel("UNKNOWN")
        self.measurements_value = QLabel("V — | I — | P —")
        self.regulation_value = QLabel("CV/CC: not reported")
        self.protocol_value = QLabel("Protocol: IDLE")
        self.stage_value = QLabel("Stage: —")
        self.csv_value = QLabel("CSV: inactive")
        self.csv_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for column, widget in enumerate((
            self.output_value, self.measurements_value, self.regulation_value,
            self.protocol_value, self.stage_value, self.csv_value,
        )):
            widget.setObjectName(f"channel{channel}Status{column}")
            status_layout.addWidget(widget, column // 3, column % 3)
        layout.addWidget(status)
        self.tabs = QTabWidget()
        self.tabs.setObjectName(f"channelProtocolTabs{channel}")
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        for name, panel in zip(self.TAB_NAMES, panels, strict=True):
            if isinstance(panel, QGroupBox):
                panel.setFlat(True)
            self.tabs.addTab(panel, name)
        layout.addWidget(self.tabs, 1)

    def update_status(
        self,
        *,
        output: str,
        voltage: str,
        current: str,
        power: str,
        protocol: str,
        stage: str,
        csv: str,
    ) -> None:
        self.output_value.setText(f"Output: {output}")
        palette = {
            "ON": "background:#b42318;color:white;font-weight:800;padding:3px;",
            "OFF": "background:#067647;color:white;font-weight:800;padding:3px;",
        }
        self.output_value.setStyleSheet(palette.get(output, "font-weight:700;padding:3px;"))
        self.measurements_value.setText(f"V {voltage} | I {current} | P {power}")
        self.protocol_value.setText(f"Protocol: {protocol}")
        self.stage_value.setText(f"Stage: {stage}")
        self.csv_value.setText(f"CSV: {csv}")
        self.csv_value.setToolTip(csv)


class CollapsiblePlotSection(QGroupBox):
    """Checkable plot section with a user-resizable two-channel splitter."""

    def __init__(self, title: str, panels: tuple[QWidget, QWidget]) -> None:
        super().__init__(title)
        self.setCheckable(True)
        self.setChecked(True)
        layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        for panel in panels:
            self.splitter.addWidget(panel)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)
        self.toggled.connect(self.splitter.setVisible)

