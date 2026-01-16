"""
PySide6 UI for MODEP Rig control.

Run with: python rig_ui.py
"""

import signal
import sys
from pathlib import Path

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
    QSlider,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QScrollArea,
    QFrame,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt, Signal, QTimer, QObject

from modep_rig import Config, Rig, ControlPort


class RigSignals(QObject):
    """Qt signals for Rig WebSocket events."""
    param_changed = Signal(str, str, float)  # label, symbol, value
    bypass_changed = Signal(str, bool)  # label, bypassed
    structural_changed = Signal(str, str)  # msg_type, raw_message


class ControlWidget(QWidget):
    """Base widget for a plugin control."""

    value_changed = Signal(str, float)  # symbol, value

    def __init__(self, control: ControlPort, parent=None):
        super().__init__(parent)
        self.control = control
        self._updating = False

    def set_value_silent(self, value: float):
        """Set value without emitting signal."""
        self._updating = True
        self._set_widget_value(value)
        self._updating = False

    def _set_widget_value(self, value: float):
        """Override in subclass."""
        pass

    def _emit_change(self, value: float):
        """Emit value change if not updating."""
        if not self._updating:
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
        self.control.value = value
        self.value_label.setText(self.control.format_value())
        self._emit_change(value)

    def _set_widget_value(self, value: float):
        self.control.value = value
        self.dial.setValue(self._value_to_slider(value))
        self.value_label.setText(self.control.format_value())


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
        self.control.value = value
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
        self.control.value = value
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
        # self.slider = QSlider(Qt.Horizontal)
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
        self.control.value = float(value)
        self.value_label.setText(self.control.format_value())
        self._emit_change(float(value))

    def _set_widget_value(self, value: float):
        self.control.value = value
        self.slider.setValue(int(value))
        self.value_label.setText(self.control.format_value())


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

    def __init__(self, effects_list: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Plugin")
        self.setMinimumSize(400, 500)

        self.selected_uri = None

        layout = QVBoxLayout(self)

        # Search/filter could be added here

        # Plugin list
        self.list_widget = QListWidget()
        for effect in effects_list:
            name = effect.get("name", "Unknown")
            uri = effect.get("uri", "")
            category = effect.get("category", [])
            cat_str = ", ".join(category) if category else "Uncategorized"

            item = QListWidgetItem(f"{name}\n  [{cat_str}]")
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
    """Widget representing a single slot in the rig."""

    clicked = Signal(int)  # slot_id
    load_requested = Signal(int)  # slot_id
    clear_requested = Signal(int)  # slot_id

    def __init__(self, slot_id: int, parent=None):
        super().__init__(parent)
        self.slot_id = slot_id
        self.is_selected = False

        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(2)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(120, 80)

        layout = QVBoxLayout(self)

        # Slot number
        self.slot_label = QLabel(f"Slot {slot_id}")
        self.slot_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.slot_label)

        # Plugin name
        self.plugin_label = QLabel("Empty")
        self.plugin_label.setAlignment(Qt.AlignCenter)
        self.plugin_label.setWordWrap(True)
        layout.addWidget(self.plugin_label)

        # Buttons
        btn_layout = QHBoxLayout()
        self.load_btn = QPushButton("Load")
        self.load_btn.clicked.connect(lambda: self.load_requested.emit(self.slot_id))
        btn_layout.addWidget(self.load_btn)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(lambda: self.clear_requested.emit(self.slot_id))
        self.clear_btn.setEnabled(False)
        btn_layout.addWidget(self.clear_btn)

        layout.addLayout(btn_layout)

        self._update_style()

    def set_plugin_name(self, name: str | None):
        if name:
            self.plugin_label.setText(name)
            self.clear_btn.setEnabled(True)
        else:
            self.plugin_label.setText("Empty")
            self.clear_btn.setEnabled(False)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self._update_style()

    def _update_style(self):
        if self.is_selected:
            self.setStyleSheet("SlotWidget { background-color: #3daee9; }")
        else:
            self.setStyleSheet("")

    def mousePressEvent(self, event):
        self.clicked.emit(self.slot_id)
        super().mousePressEvent(event)


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

        self.plugin = None
        self.control_widgets: dict[str, ControlWidget] = {}
        self.bypass_checkbox: QCheckBox | None = None
        self._updating_bypass = False

        # Placeholder
        self.placeholder = QLabel("Select a slot with a plugin to see controls")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.placeholder)

    def set_plugin(self, plugin):
        """Set the plugin to display controls for."""
        # Clear existing
        self._clear_controls()
        self.plugin = plugin

        if plugin is None:
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

        for symbol in plugin.controls:
            control = plugin.controls[symbol]
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
            self.plugin[symbol] = value

    def set_bypass_silent(self, bypassed: bool):
        """Set bypass checkbox without emitting signal."""
        if self.bypass_checkbox:
            self._updating_bypass = True
            self.bypass_checkbox.setChecked(bypassed)
            self._updating_bypass = False

    def _on_bypass_changed(self, state):
        """Handle bypass checkbox change."""
        if self._updating_bypass:
            return
        if self.plugin:
            self.plugin.bypass(state)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, rig: Rig):
        super().__init__()
        self.rig = rig
        self.selected_slot = 0

        # Setup signals for thread-safe UI updates
        self.rig_signals = RigSignals()
        self.rig_signals.param_changed.connect(self._on_ws_param_changed)
        self.rig_signals.bypass_changed.connect(self._on_ws_bypass_changed)
        self.rig_signals.structural_changed.connect(self._on_ws_structural_changed)

        # Connect rig callbacks to emit signals
        self.rig.set_callbacks(
            on_param_change=lambda label, sym, val: self.rig_signals.param_changed.emit(label, sym, val),
            on_bypass_change=lambda label, bp: self.rig_signals.bypass_changed.emit(label, bp),
            on_structural_change=lambda typ, msg: self.rig_signals.structural_changed.emit(typ, msg),
        )

        self.setWindowTitle("MODEP Rig Controller")
        self.setMinimumSize(800, 600)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Left side - slots
        left_panel = QVBoxLayout()

        slots_label = QLabel("<b>Effect Chain</b>")
        left_panel.addWidget(slots_label)

        self.slot_widgets: list[SlotWidget] = []
        for i in range(len(rig)):
            slot_widget = SlotWidget(i)
            slot_widget.clicked.connect(self._on_slot_clicked)
            slot_widget.load_requested.connect(self._on_load_requested)
            slot_widget.clear_requested.connect(self._on_clear_requested)
            self.slot_widgets.append(slot_widget)
            left_panel.addWidget(slot_widget)

        left_panel.addStretch()

        # Clear all button
        clear_all_btn = QPushButton("Clear All")
        clear_all_btn.clicked.connect(self._on_clear_all)
        left_panel.addWidget(clear_all_btn)

        main_layout.addLayout(left_panel)

        # Right side - controls panel
        self.controls_panel = ControlsPanel()
        self.controls_panel.setMinimumWidth(500)
        main_layout.addWidget(self.controls_panel, stretch=1)

        # Initial state
        self._refresh_slots()
        self._select_slot(0)

    def _refresh_slots(self):
        """Update slot widgets from rig state."""
        for i, slot_widget in enumerate(self.slot_widgets):
            slot = self.rig[i]
            if slot.plugin:
                slot_widget.set_plugin_name(slot.plugin.name)
            else:
                slot_widget.set_plugin_name(None)

    def _select_slot(self, slot_id: int):
        """Select a slot and show its controls."""
        self.selected_slot = slot_id

        for i, sw in enumerate(self.slot_widgets):
            sw.set_selected(i == slot_id)

        slot = self.rig[slot_id]
        self.controls_panel.set_plugin(slot.plugin)

    def _on_slot_clicked(self, slot_id: int):
        self._select_slot(slot_id)

    def _on_load_requested(self, slot_id: int):
        """Show plugin selector and load selected plugin."""
        dialog = PluginSelectorDialog(self.rig.client.effects_list, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_uri:
            self.rig[slot_id] = dialog.selected_uri
            self._refresh_slots()
            self._select_slot(slot_id)

    def _on_clear_requested(self, slot_id: int):
        """Clear the specified slot."""
        self.rig[slot_id] = None
        self._refresh_slots()
        self._select_slot(slot_id)

    def _on_clear_all(self):
        """Clear all slots."""
        self.rig.clear()
        self._refresh_slots()
        self._select_slot(self.selected_slot)

    # =========================================================================
    # WebSocket event handlers (thread-safe via Qt signals)
    # =========================================================================

    def _on_ws_param_changed(self, label: str, symbol: str, value: float):
        """Handle parameter change from WebSocket - update UI."""
        # Find which slot has this plugin
        for i, slot in enumerate(self.rig.slots):
            if slot.plugin and slot.plugin.label == label:
                # If this is the selected slot, update control widget
                if i == self.selected_slot and symbol in self.controls_panel.control_widgets:
                    widget = self.controls_panel.control_widgets[symbol]
                    widget.set_value_silent(value)
                break

    def _on_ws_bypass_changed(self, label: str, bypassed: bool):
        """Handle bypass change from WebSocket - update UI."""
        # Find which slot has this plugin
        for i, slot in enumerate(self.rig.slots):
            if slot.plugin and slot.plugin.label == label:
                # If this is the selected slot, update bypass checkbox
                if i == self.selected_slot:
                    self.controls_panel.set_bypass_silent(bypassed)
                break

    def _on_ws_structural_changed(self, msg_type: str, _raw_message: str):
        """Handle structural change from WebSocket - refresh UI."""
        print(f"⚠️ UI: Structural change: {msg_type}")
        # Refresh slots display - structure may have changed externally
        self._refresh_slots()
        # Re-select current slot to refresh controls panel
        self._select_slot(self.selected_slot)


def main():
    # Load config
    config = Config.load("config.toml")

    # Override server URL if needed
    import argparse
    parser = argparse.ArgumentParser(description="MODEP Rig Controller")
    parser.add_argument("--server", "-s", default=None, help="MOD server URL")
    args = parser.parse_args()

    if args.server:
        config.server.url = args.server

    # Create rig
    print("Connecting to MOD server...")
    rig = Rig(config)

    # Create and run app
    app = QApplication(sys.argv)
    
    # 2. Обробка сигналу Ctrl+C
    # Використовуємо стандартний обробник сигналу Python
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = MainWindow(rig)
    window.show()

    # 3. Додаємо таймер (важливо для Linux/Windows)
    # Qt блокує виконання Python, тому без таймера Ctrl+C спрацює 
    # лише після того, як ви якось взаємодієте з вікном.
    timer = QTimer()
    timer.start(500)  # Перевіряти кожні 500 мс
    timer.timeout.connect(lambda: None)  # Порожня функція просто для "пробудження" інтерпретатора

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
