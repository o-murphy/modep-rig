"""
PySide6 UI for MODEP Rig control.

Run with: python rig_ui.py
"""

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
    QSlider,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt, Signal

from modep_rig import Config, Rig, ControlPort


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

        # Slider
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.SLIDER_STEPS)
        self.slider.setValue(self._value_to_slider(control.value))
        self.slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.slider)

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
        self.slider.setValue(self._value_to_slider(value))
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
        self.slider = QSlider(Qt.Horizontal)
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

        bypass_cb = QCheckBox("Bypass")
        bypass_cb.stateChanged.connect(self._on_bypass_changed)
        header.addWidget(bypass_cb)
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

    def _on_bypass_changed(self, state):
        """Handle bypass checkbox change."""
        if self.plugin:
            self.plugin.bypass(state == Qt.Checked)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, rig: Rig):
        super().__init__()
        self.rig = rig
        self.selected_slot = 0

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
    window = MainWindow(rig)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
