"""Compact operator dashboard shell; contains no instrument logic."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


NAVIGATION_ITEMS = (
    "Dashboard",
    "Manual Control",
    "Experiments",
    "Live Data",
    "Recorded Results",
    "Files & Templates",
    "Settings",
)

EXPERIMENT_ITEMS = (
    "Chronopotentiometry (CP)",
    "Chronoamperometry (CA)",
    "Voltage Sweep / LSV",
    "Current Sweep",
    "Polarisation",
    "Custom Sequence",
)


def scroll_page(widget: QWidget, name: str) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName(name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


class OperatorDashboard(QWidget):
    """Navigation and presentation only; controllers remain owned by MainWindow."""

    navigation_changed = Signal(str)
    experiment_changed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("operatorDashboard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.navigation = QListWidget()
        self.navigation.setObjectName("primaryNavigation")
        self.navigation.setFixedWidth(176)
        self.navigation.setSpacing(2)
        for label in NAVIGATION_ITEMS:
            QListWidgetItem(label, self.navigation)
        self.navigation.setCurrentRow(0)
        self.pages = QStackedWidget()
        self.pages.setObjectName("navigationPages")
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.navigation.currentRowChanged.connect(self._navigation_selected)

    def add_page(self, widget: QWidget) -> None:
        self.pages.addWidget(widget)

    def show_page(self, name: str) -> None:
        try:
            row = NAVIGATION_ITEMS.index(name)
        except ValueError:
            return
        self.navigation.setCurrentRow(row)

    def _navigation_selected(self, row: int) -> None:
        if 0 <= row < self.pages.count():
            self.pages.setCurrentIndex(row)
            self.navigation_changed.emit(NAVIGATION_ITEMS[row])


class ExperimentSelector(QWidget):
    """Show exactly one channel experiment editor at a time."""

    selection_changed = Signal(int, int)

    def __init__(self, editors: dict[int, tuple[QWidget, ...]]) -> None:
        super().__init__()
        self.setObjectName("experimentSelectorPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Channel"))
        self.channel_selector = QComboBox()
        self.channel_selector.setObjectName("experimentChannelSelector")
        self.channel_selector.addItems(("CH1", "CH2"))
        selector_row.addWidget(self.channel_selector)
        selector_row.addWidget(QLabel("Experiment"))
        self.experiment_selector = QComboBox()
        self.experiment_selector.setObjectName("experimentTypeSelector")
        self.experiment_selector.addItems(EXPERIMENT_ITEMS)
        selector_row.addWidget(self.experiment_selector, 1)
        layout.addLayout(selector_row)
        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("experimentEditorStack")
        for channel in (1, 2):
            for editor in editors[channel]:
                page = scroll_page(editor, f"ch{channel}ExperimentScroll")
                self.editor_stack.addWidget(page)
        layout.addWidget(self.editor_stack, 1)
        self.channel_selector.currentIndexChanged.connect(self._select)
        self.experiment_selector.currentIndexChanged.connect(self._select)
        self._select()

    @property
    def selected_channel(self) -> int:
        return self.channel_selector.currentIndex() + 1

    @property
    def selected_experiment(self) -> int:
        return self.experiment_selector.currentIndex()

    def select(self, channel: int, experiment: int) -> None:
        self.channel_selector.setCurrentIndex(channel - 1)
        self.experiment_selector.setCurrentIndex(experiment)
        self._select()

    def _select(self, *_args: object) -> None:
        channel_index = max(0, self.channel_selector.currentIndex())
        experiment_index = max(0, self.experiment_selector.currentIndex())
        self.editor_stack.setCurrentIndex(channel_index * len(EXPERIMENT_ITEMS) + experiment_index)
        self.selection_changed.emit(channel_index + 1, experiment_index)


class LiveDataPage(QWidget):
    """Vertically stacked channel plots with local maximise/restore controls."""

    def __init__(self, channel_panels: dict[int, QWidget]) -> None:
        super().__init__()
        self.setObjectName("liveDataPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sync_time_zoom = True
        hint = QLabel(
            "Voltage (blue) and Current (orange) use existing samples only. "
            "Right-drag to zoom; click near a point for details. Time zoom is synchronised."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName("liveDataVerticalSplitter")
        self._containers: dict[int, QWidget] = {}
        self._maximised_channel: int | None = None
        for channel in (1, 2):
            container = QWidget()
            container.setObjectName(f"channel{channel}LivePlotContainer")
            box = QVBoxLayout(container)
            box.setContentsMargins(0, 0, 0, 0)
            top = QHBoxLayout()
            title = QLabel(f"CH{channel} live data")
            title.setProperty("role", "sectionTitle")
            top.addWidget(title)
            top.addStretch(1)
            button = QPushButton("Maximise")
            button.setObjectName(f"channel{channel}MaximisePlot")
            button.clicked.connect(lambda _checked=False, ch=channel: self.toggle_maximise(ch))
            top.addWidget(button)
            container.maximise_button = button  # type: ignore[attr-defined]
            box.addLayout(top)
            box.addWidget(channel_panels[channel], 1)
            self._containers[channel] = container
            self.splitter.addWidget(container)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([1, 1])
        layout.addWidget(self.splitter, 1)

    def toggle_maximise(self, channel: int) -> None:
        if self._maximised_channel == channel:
            self._maximised_channel = None
            for item in self._containers.values():
                item.show()
                item.maximise_button.setText("Maximise")  # type: ignore[attr-defined]
            self.splitter.setSizes([1, 1])
            return
        self._maximised_channel = channel
        for selected, item in self._containers.items():
            item.setVisible(selected == channel)
            item.maximise_button.setText("Restore")  # type: ignore[attr-defined]

    def set_active_channels(self, channels: set[int]) -> None:
        if self._maximised_channel is not None:
            return
        if len(channels) == 1:
            only = next(iter(channels))
            self._containers[only].show()
            self._containers[3 - only].hide()
        else:
            for item in self._containers.values():
                item.show()
            self.splitter.setSizes([1, 1])


def card(title: str, body: str, action_text: str | None = None,
         action: Callable[[], None] | None = None) -> QWidget:
    widget = QFrame()
    widget.setProperty("surface", "card")
    layout = QVBoxLayout(widget)
    heading = QLabel(title)
    heading.setProperty("role", "sectionTitle")
    layout.addWidget(heading)
    text = QLabel(body)
    text.setWordWrap(True)
    layout.addWidget(text)
    if action_text is not None:
        button = QPushButton(action_text)
        if action is not None:
            button.clicked.connect(action)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        widget.action_button = button  # type: ignore[attr-defined]
    return widget
