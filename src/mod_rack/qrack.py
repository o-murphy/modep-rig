"""
PySide6 UI for MODEP Rack control.

Run with: python qrack.py
"""

import signal
import sys
from pathlib import Path

from mod_rack.client import ParamSetBypassEvent, ParamSetEvent
from mod_rack.plugin import Plugin

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLabel,
    QDial,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QScrollArea,
    QFrame,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QMenu,
)
from PySide6.QtCore import Qt, Signal, QTimer, QObject

from mod_rack import Config, Rack, ControlPort
from mod_rack.rack import PluginSlot


class RackSignals(QObject):
    """Qt signals for Rack WebSocket events."""

    slot_added = Signal(object)  # slot
    slot_removed = Signal(str)  # label
    order_changed = Signal(list)  # order (list of labels)


class ControlWidget(QWidget):
    """Base widget for a plugin control."""

    value_changed = Signal(str, float)  # symbol, value

    def __init__(self, control: ControlPort, parent=None):
        super().__init__(parent)
        self.control = control

    def set_value_silent(self, value: float):
        """Set value without emitting signal."""
        self.blockSignals(True)
        self._set_widget_value(value)
        self.blockSignals(False)

    def _set_widget_value(self, value: float):
        """Override in subclass."""
        pass

    def _emit_change(self, value: float):
        """Emit value change if not updating."""
        self.value_changed.emit(self.control.symbol, value)


class KnobControl(ControlWidget):
    """Slider control for continuous values."""

    SLIDER_STEPS = 1000

    def __init__(self, control: ControlPort, parent=None):
        super().__init__(control, parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Label
        self.label = QLabel(control.name)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        # Dial
        self.dial = QDial()
        self.dial.setNotchesVisible(True)
        self.dial.setNotchTarget(100.0)
        self.dial.setWrapping(False)
        self.dial.setRange(0, self.SLIDER_STEPS)
        self.dial.setValue(self._value_to_slider(control.value))
        self.dial.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.dial)

        # Value display
        self.value_label = QLabel(control.format_value())
        self.value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.value_label)

    def _value_to_slider(self, value: float) -> int:
        """Convert actual value to slider position using normalize."""
        normalized = self.control.normalize(value)
        return int(normalized * self.SLIDER_STEPS)

    def _slider_to_value(self, pos: int) -> float:
        """Convert slider position to actual value using denormalize."""
        normalized = pos / self.SLIDER_STEPS
        return self.control.denormalize(normalized)

    def _on_slider_changed(self, pos: int):
        value = self._slider_to_value(pos)
        self.value_label.setText(self.control.format_value(value))
        self._emit_change(value)

    def _set_widget_value(self, value: float):
        self.dial.setValue(self._value_to_slider(value))
        self.value_label.setText(self.control.format_value(value))


class ToggleControl(ControlWidget):
    """Checkbox for toggle controls."""

    def __init__(self, control: ControlPort, parent=None):
        super().__init__(control, parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.checkbox = QCheckBox(control.name)
        self.checkbox.setChecked(control.value >= 0.5)
        self.checkbox.stateChanged.connect(self._on_state_changed)
        layout.addWidget(self.checkbox)

    def _on_state_changed(self, state):
        value = 1.0 if state == Qt.Checked else 0.0
        self.control.value = value
        self._emit_change(value)

    def _set_widget_value(self, value: float):
        self.checkbox.setChecked(value >= 0.5)


class EnumControl(ControlWidget):
    """ComboBox for enumeration controls."""

    def __init__(self, control: ControlPort, parent=None):
        super().__init__(control, parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Label
        self.label = QLabel(control.name)
        layout.addWidget(self.label)

        # ComboBox
        self.combo = QComboBox()
        for sp in control.scale_points:
            self.combo.addItem(sp.label, sp.value)

        # Set current value
        current_idx = self._value_to_index(control.value)
        if current_idx >= 0:
            self.combo.setCurrentIndex(current_idx)

        self.combo.currentIndexChanged.connect(self._on_index_changed)
        layout.addWidget(self.combo)

    def _value_to_index(self, value: float) -> int:
        for i, sp in enumerate(self.control.scale_points):
            if sp.value == value:
                return i
        return 0

    def _on_index_changed(self, index: int):
        if index >= 0:
            value = self.combo.itemData(index)
            self.control.value = value
            self._emit_change(value)

    def _set_widget_value(self, value: float):
        idx = self._value_to_index(value)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)


class IntegerControl(ControlWidget):
    """Slider for integer controls (non-enum)."""

    def __init__(self, control: ControlPort, parent=None):
        super().__init__(control, parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Label
        self.label = QLabel(control.name)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        # Slider with integer steps
        self.slider = QDial()

        self.slider.setRange(int(control.minimum), int(control.maximum))
        self.slider.setValue(int(control.value))
        self.slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.slider)

        # Value display
        self.value_label = QLabel(control.format_value())
        self.value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.value_label)

    def _on_slider_changed(self, value: int):
        self.value_label.setText(self.control.format_value(value))
        self._emit_change(float(value))

    def _set_widget_value(self, value: float):
        self.slider.setValue(int(value))
        self.value_label.setText(self.control.format_value(value))


def create_control_widget(control: ControlPort, parent=None) -> ControlWidget:
    """Factory function to create appropriate widget for control type."""
    if control.is_toggled:
        return ToggleControl(control, parent)
    if control.is_enumeration:
        return EnumControl(control, parent)
    if control.is_integer and not control.is_enumeration:
        return IntegerControl(control, parent)
    return KnobControl(control, parent)


class PluginSelectorDialog(QDialog):
    """Dialog to select a plugin from available effects."""

    def __init__(self, rack: Rack, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Plugin")
        self.setMinimumSize(400, 500)

        self.selected_uri = None

        layout = QVBoxLayout(self)

        # Plugin list - show only whitelisted plugins
        self.list_widget = QListWidget()

        for p_config in rack.config.plugins:
            name = p_config.name
            uri = p_config.uri
            category = p_config.category or "General"

            item = QListWidgetItem(f"{name}\n  [{category}]")
            item.setData(Qt.UserRole, uri)
            self.list_widget.addItem(item)

        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list_widget)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_double_click(self, item):
        self.selected_uri = item.data(Qt.UserRole)
        self.accept()

    def _on_accept(self):
        current = self.list_widget.currentItem()
        if current:
            self.selected_uri = current.data(Qt.UserRole)
        self.accept()


class SlotWidget(QFrame):
    """Widget representing a single slot in the rack."""

    clicked = Signal(str)  # label
    remove_requested = Signal(str)  # label
    replace_requested = Signal(str)  # label
    dropped = Signal(str, int)  # source_label, destination_index

    def __init__(self, label: str, index: int, plugin_name: str, parent=None):
        super().__init__(parent)
        self.slot_label_id = label  # Plugin label (unique ID)
        self.index = index
        self.is_selected = False

        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(2)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(120, 80)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.setAcceptDrops(True)
        self._drag_start_pos = None

        layout = QVBoxLayout(self)

        # Slot number
        self.slot_num_label = QLabel(f"Slot {index}")
        self.slot_num_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.slot_num_label)

        # Plugin name
        self.plugin_label = QLabel(plugin_name)
        self.plugin_label.setAlignment(Qt.AlignCenter)
        self.plugin_label.setWordWrap(True)
        layout.addWidget(self.plugin_label)

        self._update_style()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self._update_style()

    def _update_style(self):
        if self.is_selected:
            self.setStyleSheet("SlotWidget { background-color: #3daee9; }")
        else:
            self.setStyleSheet("")

    def _show_context_menu(self, pos):
        """Show context menu for slot operations."""
        menu = QMenu()

        replace_action = menu.addAction("Replace Plugin")
        replace_action.triggered.connect(
            lambda: self.replace_requested.emit(self.slot_label_id)
        )

        remove_action = menu.addAction("Remove Plugin")
        remove_action.triggered.connect(
            lambda: self.remove_requested.emit(self.slot_label_id)
        )

        menu.exec(self.mapToGlobal(pos))

    def mousePressEvent(self, event):
        # emit click and store drag start position
        print(f"MOUSE_PRESS: label={self.slot_label_id} pos={event.pos()}")
        self.clicked.emit(self.slot_label_id)
        self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_start_pos is not None:
            distance = (event.pos() - self._drag_start_pos).manhattanLength()
            print(f"MOUSE_MOVE: label={self.slot_label_id} distance={distance}")
            if distance >= QApplication.startDragDistance():
                from PySide6.QtGui import QDrag, QPixmap
                from PySide6.QtCore import QMimeData

                drag = QDrag(self)
                mime = QMimeData()
                # put both custom data and plain text for robustness
                mime.setData(
                    "application/x-slot-label", self.slot_label_id.encode("utf-8")
                )
                mime.setText(self.slot_label_id)
                drag.setMimeData(mime)

                # optional pixmap
                pix = QPixmap(self.size())
                self.render(pix)
                drag.setPixmap(pix)

                print(f"START_DRAG: label={self.slot_label_id}")
                # change cursor to closed hand while dragging
                QApplication.setOverrideCursor(Qt.ClosedHandCursor)
                result = drag.exec(Qt.MoveAction)
                QApplication.restoreOverrideCursor()
                print(f"DRAG_RESULT: label={self.slot_label_id} result={result}")

        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat("application/x-slot-label") or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat("application/x-slot-label") or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat("application/x-slot-label") or mime.hasText():
            if mime.hasText():
                src_label = mime.text()
            else:
                src_label = bytes(mime.data("application/x-slot-label")).decode("utf-8")
            # debug log
            print(f"DROP_EVENT: src_label={src_label} dest_index={self.index}")
            # emit source label and this widget's index as destination
            self.dropped.emit(src_label, self.index)
            event.acceptProposedAction()
        else:
            event.ignore()


class ControlsPanel(QScrollArea):
    """Panel showing controls for selected plugin."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setAlignment(Qt.AlignTop)
        self.setWidget(self.container)

        self.plugin: Plugin | None = None
        self.current_label: str | None = None
        self.control_widgets: dict[str, ControlWidget] = {}
        self.bypass_checkbox: QCheckBox | None = None

        # Placeholder
        self.placeholder = QLabel("Select a plugin to see controls")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.placeholder)

    def set_plugin(self, plugin, label: str | None = None):
        """Set the plugin to display controls for."""
        # Clear existing
        self._clear_controls()
        self.plugin = plugin
        self.current_label = label

        if plugin is None:
            self.placeholder.setText("Select a plugin to see controls")
            self.placeholder.show()
            return

        self.placeholder.hide()

        # Plugin name and bypass
        header = QHBoxLayout()
        name_label = QLabel(f"<b>{plugin.name}</b>")
        header.addWidget(name_label)

        self.bypass_checkbox = QCheckBox("Bypass")
        # Set initial state from plugin
        bypassed = getattr(plugin, "_bypassed", False)
        self.bypass_checkbox.setChecked(bypassed)
        self.bypass_checkbox.toggled.connect(self._on_bypass_changed)
        header.addWidget(self.bypass_checkbox)

        header.addStretch()

        self.layout.addLayout(header)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        self.layout.addWidget(line)

        # Controls grid
        controls_group = QGroupBox("Controls")
        grid = QGridLayout(controls_group)

        row, col = 0, 0
        max_cols = 3

        for symbol in plugin:
            control = plugin[symbol]
            widget = create_control_widget(control)
            widget.value_changed.connect(self._on_control_changed)
            self.control_widgets[symbol] = widget

            grid.addWidget(widget, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        self.layout.addWidget(controls_group)
        self.layout.addStretch()

    def _clear_controls(self):
        """Remove all control widgets."""
        for widget in self.control_widgets.values():
            widget.deleteLater()
        self.control_widgets.clear()
        self.bypass_checkbox = None

        # Clear layout
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() and item.widget() != self.placeholder:
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        self.layout.addWidget(self.placeholder)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _on_control_changed(self, symbol: str, value: float):
        """Handle control value change."""
        if self.plugin:
            self.plugin.param_set(symbol, value)

    def set_bypass_silent(self, bypassed: bool):
        """Set bypass checkbox without emitting signal."""
        if self.bypass_checkbox:
            self.blockSignals(True)
            self.bypass_checkbox.setChecked(bypassed)
            self.blockSignals(False)

    def _on_bypass_changed(self, state):
        """Handle bypass checkbox change."""
        if self.plugin:
            self.plugin.bypass(state)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, rack: Rack):
        super().__init__()
        self.rack = rack
        self.selected_label: str | None = None

        # Setup signals for thread-safe UI updates
        self.rack_signals = RackSignals()
        self.rack_signals.slot_added.connect(self._on_ws_slot_added)
        self.rack_signals.slot_removed.connect(self._on_ws_slot_removed)
        self.rack_signals.order_changed.connect(self._on_ws_order_changed)

        self.rack.client.ws.on(ParamSetBypassEvent, self._on_ws_bypass_changed)
        self.rack.client.ws.on(ParamSetEvent, self._on_ws_param_changed)

        # Connect rack callbacks to emit signals
        self.rack.set_callbacks(
            on_slot_added=lambda slot: self.rack_signals.slot_added.emit(slot),
            on_slot_removed=lambda label: self.rack_signals.slot_removed.emit(label),
            on_order_change=lambda order: self.rack_signals.order_changed.emit(order),
        )

        self.setWindowTitle("MODEP Rack Controller")
        self.setMinimumSize(800, 600)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Left side - slots
        self.left_panel = QVBoxLayout()

        slots_label = QLabel("<b>Effect Chain</b>")
        self.left_panel.addWidget(slots_label)

        # Container for slot widgets
        self.slots_container = QVBoxLayout()
        self.left_panel.addLayout(self.slots_container)

        self.slot_widgets: list[SlotWidget] = []

        # Add plugin button (no empty slots anymore)
        self.add_plugin_btn = QPushButton("+ Add Plugin")
        self.add_plugin_btn.clicked.connect(self._on_add_plugin)
        self.left_panel.addWidget(self.add_plugin_btn)

        self.left_panel.addStretch()

        # Clear all button
        clear_all_btn = QPushButton("Clear All")
        clear_all_btn.clicked.connect(self._on_clear_all)
        self.left_panel.addWidget(clear_all_btn)

        main_layout.addLayout(self.left_panel)

        # Rackht side - controls panel
        self.controls_panel = ControlsPanel()
        self.controls_panel.setMinimumWidth(500)
        main_layout.addWidget(self.controls_panel, stretch=1)

        # Initial state
        self._rebuild_slot_widgets()

    def __del__(self):
        self.rack.client.ws.off(ParamSetBypassEvent, self._on_ws_bypass_changed)
        self.rack.client.ws.off(ParamSetEvent, self._on_ws_param_changed)

    def _rebuild_slot_widgets(self):
        """Rebuild all slot widgets from rack state."""
        # Clear existing widgets
        for widget in self.slot_widgets:
            widget.deleteLater()
        self.slot_widgets.clear()

        # Clear layout
        while self.slots_container.count():
            item = self.slots_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Create new widgets for each slot
        for i, slot in enumerate(self.rack.slots):
            plugin_name = slot.plugin.name
            slot_widget = SlotWidget(slot.label, i, plugin_name)
            slot_widget.clicked.connect(self._on_slot_clicked)
            slot_widget.remove_requested.connect(self._on_remove_plugin)
            slot_widget.replace_requested.connect(self._on_replace_plugin)
            slot_widget.dropped.connect(self._on_slot_dropped)
            self.slot_widgets.append(slot_widget)
            self.slots_container.addWidget(slot_widget)

        # Update selection
        if self.selected_label and self.rack.get_slot_by_label(self.selected_label):
            self._select_slot(self.selected_label)
        elif self.rack.slots:
            self._select_slot(self.rack.slots[0].label)
        else:
            self.selected_label = None
            self.controls_panel.set_plugin(None)

    def _select_slot(self, label: str):
        """Select a slot and show its controls."""
        self.selected_label = label

        for sw in self.slot_widgets:
            sw.set_selected(sw.slot_label_id == label)

        slot = self.rack.get_slot_by_label(label)
        if slot:
            self.controls_panel.set_plugin(slot.plugin, label)
        else:
            self.controls_panel.set_plugin(None)

    def _on_slot_clicked(self, label: str):
        """Handle slot click - select it."""
        self._select_slot(label)

    def _on_add_plugin(self):
        """Add a new plugin (request via REST, wait for WS feedback)."""
        dialog = PluginSelectorDialog(self.rack, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_uri:
            label = self.rack.request_add_plugin(dialog.selected_uri)
            if label:
                print(f"Requested add plugin, label={label}")
            else:
                print("Failed to request add plugin")

    def _on_remove_plugin(self, label: str):
        """Remove plugin (request via REST, wait for WS feedback)."""
        success = self.rack.request_remove_plugin(label)
        if success:
            print(f"Requested remove plugin {label}")
        else:
            print(f"Failed to request remove plugin {label}")

    def _on_replace_plugin(self, label: str):
        """Replace plugin - remove old, add new."""
        dialog = PluginSelectorDialog(self.rack, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_uri:
            # Preserve slot index: remove old, then request add at same index
            slot = self.rack.get_slot_by_label(label)
            insert_idx = self.rack.slots.index(slot) if slot else None
            # Request remove first
            self.rack.request_remove_plugin(label)
            # Request add at the same index (will be moved when WS feedback arrives)
            if insert_idx is not None:
                self.rack.request_add_plugin_at(dialog.selected_uri, insert_idx)
            else:
                self.rack.request_add_plugin(dialog.selected_uri)

            return

    def _on_clear_all(self):
        """Clear all plugins."""
        self.rack.clear()
        # Immediately update UI to reflect cleared state
        self._rebuild_slot_widgets()

    def _on_slot_dropped(self, src_label: str, dest_index: int):
        """Handle drag-and-drop reorder: move src slot to dest index."""
        print(f"ON_SLOT_DROPPED: src_label={src_label} dest_index={dest_index}")
        src_slot = self.rack.get_slot_by_label(src_label)
        if not src_slot:
            return
        from_idx = self.rack.slots.index(src_slot)
        to_idx = dest_index
        print(f"ON_SLOT_DROPPED: from_idx={from_idx} to_idx={to_idx}")
        if from_idx == to_idx:
            return
        # Use rack.move_slot which handles reconnect
        self.rack.move_slot(from_idx, to_idx)
        # Rebuild UI to reflect new order and keep selection on moved slot
        self._rebuild_slot_widgets()
        self._select_slot(src_label)

    # =========================================================================
    # WebSocket event handlers (thread-safe via Qt signals)
    # =========================================================================

    def _on_ws_param_changed(self, event: ParamSetEvent):
        """Handle parameter change from WebSocket - update UI."""
        # If this is the selected slot, update control widget
        if (
            event.label == self.selected_label
            and event.symbol in self.controls_panel.control_widgets
        ):
            widget = self.controls_panel.control_widgets[event.symbol]
            widget.set_value_silent(event.value)

    def _on_ws_bypass_changed(self, event: ParamSetBypassEvent):
        """Handle bypass change from WebSocket - update UI."""
        if event.label == self.selected_label:
            self.controls_panel.set_bypass_silent(event.bypassed)

    def _on_ws_slot_added(self, slot: PluginSlot):
        """Handle slot added from WebSocket - rebuild UI."""
        print(f"UI: Slot added: {slot.label}")
        self._rebuild_slot_widgets()
        # Select the new slot
        self._select_slot(slot.label)

    def _on_ws_slot_removed(self, label: str):
        """Handle slot removed from WebSocket - rebuild UI."""
        print(f"UI: Slot removed: {label}")
        self._rebuild_slot_widgets()

    def _on_ws_order_changed(self, order: list):
        """Handle order change from WebSocket - rebuild UI."""
        print(f"UI: Order changed: {order}")
        self._rebuild_slot_widgets()

    def closeEvent(self, event):
        """Called when user closes window."""
        print("Closing rack connection...")
        event.accept()


def main():
    # Load config

    # Override server URL if needed
    import argparse

    parser = argparse.ArgumentParser(description="MODEP Rack Controller")
    parser.add_argument("--server", "-s", default=None, help="MOD server URL")
    parser.add_argument(
        "--config", "-c", help="Config", type=Path, default="config.toml"
    )
    parser.add_argument(
        "--master", "-m", help="Master", action="store_true"
    )
    args = parser.parse_args()

    config = Config.load(args.config)

    if args.server:
        config.server.url = args.server

    # Create rack (do not force reset on init — build state from WebSocket)
    print("Connecting to MOD server...")
    rack = Rack(config, prevent_normalization=not args.master)

    # Create and run app
    app = QApplication(sys.argv)

    # Handle Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = MainWindow(rack)
    if args.master:
        window.setWindowTitle(window.windowTitle() + " (MASTER)")
    window.show()

    # Timer for Ctrl+C on Linux/Windows
    timer = QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
