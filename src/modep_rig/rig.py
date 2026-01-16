from functools import wraps
from typing import Callable, SupportsIndex

from modep_rig.config import Config, PluginConfig
from modep_rig.client import Client
from modep_rig.plugin import Plugin, Port


# Type aliases for callbacks
OnParamChangeCallback = Callable[[str, str, float], None]  # label, symbol, value
OnBypassChangeCallback = Callable[[str, bool], None]  # label, bypassed
OnStructuralChangeCallback = Callable[[str, str], None]  # msg_type, raw_message


def suppress_structural(method):
    """Decorator to suppress structural change callbacks during method execution."""
    @wraps(method)
    def wrapper(self: "Rig", *args, **kwargs):
        self.client.ws.suppress_structural(True)
        try:
            return method(self, *args, **kwargs)
        finally:
            self.client.ws.suppress_structural(False)
    return wrapper


__all__ = ["Slot", "HardwareSlot", "Rig"]


# =============================================================================
# Slots
# =============================================================================


class Slot:
    """Слот для плагіна в ланцюгу ефектів"""

    def __init__(self, rig: "Rig", slot_id: int):
        self.rig = rig
        self.id = slot_id
        self.plugin: Plugin | None = None

    @property
    def inputs(self) -> list[str]:
        if self.plugin:
            return [p.graph_path for p in self.plugin.inputs]
        return []

    @property
    def outputs(self) -> list[str]:
        if self.plugin:
            return [p.graph_path for p in self.plugin.outputs]
        return []

    @property
    def is_empty(self) -> bool:
        return self.plugin is None

    @property
    def is_stereo(self) -> bool:
        """Визначає чи слот працює в стерео режимі.

        Пріоритет:
        1. Явний mode в конфізі плагіна
        2. Кількість портів (2+ = stereo)
        """
        if not self.plugin:
            return False

        # Перевіряємо конфіг плагіна
        plugin_config = self.rig.config.get_plugin_by_uri(self.plugin.uri)
        if plugin_config and plugin_config.mode:
            return plugin_config.mode == "stereo"

        # Автовизначення по кількості портів
        return len(self.plugin.outputs) >= 2

    def load(self, uri: str, x: int = 500, y: int = 400) -> Plugin:
        """Завантажує плагін в слот (без reconnect)"""
        # Перевіряємо чи плагін підтримується
        plugin_config = self.rig.config.get_plugin_by_uri(uri)
        if not plugin_config:
            raise ValueError(f"Plugin not supported: {uri}")

        self._unload_internal()

        base_label = self._label_from_uri(uri)
        label = f"{base_label}_{self.id}"

        result = self.rig.client.effect_add(label, uri, x * (self.id + 1), y)

        if not result or not isinstance(result, dict) or not result.get("valid"):
            raise Exception(f"Failed to load plugin: {uri}")

        audio = result.get("ports", {}).get("audio", {})

        # Отримуємо всі порти з результату
        all_inputs = [
            Port(
                symbol=p["symbol"], name=p["name"], graph_path=f"{label}/{p['symbol']}"
            )
            for p in audio.get("input", [])
        ]

        all_outputs = [
            Port(
                symbol=p["symbol"], name=p["name"], graph_path=f"{label}/{p['symbol']}"
            )
            for p in audio.get("output", [])
        ]

        # Застосовуємо override якщо є в конфізі
        if plugin_config.inputs is not None:
            inputs = [p for p in all_inputs if p.symbol in plugin_config.inputs]
        else:
            inputs = all_inputs

        if plugin_config.outputs is not None:
            outputs = [p for p in all_outputs if p.symbol in plugin_config.outputs]
        else:
            outputs = all_outputs

        self.plugin = Plugin(
            slot=self,
            uri=uri,
            label=label,
            name=result.get("name", base_label),
            inputs=inputs,
            outputs=outputs,
        )

        # Load control metadata
        effect_data = self.rig.client.effect_get(uri)
        if effect_data:
            self.plugin._load_controls(effect_data)

        print(f"ADDED {self.id}: {self.plugin.label}: {self.plugin}")
        return self.plugin

    def load_by_name(self, name: str, x: int = 500, y: int = 400) -> Plugin:
        """Завантажує плагін за ім'ям з конфігурації"""
        plugin_config = self.rig.config.get_plugin_by_name(name)
        if not plugin_config:
            raise ValueError(f"Plugin '{name}' not found in config")
        return self.load(plugin_config.uri, x, y)

    def unload(self):
        """Вивантажує плагін і запускає reconnect"""
        self._unload_internal()
        self.rig.reconnect()

    def _unload_internal(self):
        """Вивантажує плагін БЕЗ reconnect"""
        if self.plugin:
            self.rig.client.effect_remove(self.plugin.label)
            self.plugin = None

    @staticmethod
    def _label_from_uri(uri: str) -> str:
        # Видаляємо fragment (#...) і беремо останню частину шляху
        path = uri.split("#")[0].rstrip("/")
        label = path.split("/")[-1]
        # Замінюємо недопустимі символи
        return label.replace("#", "_").replace(" ", "_")

    def __repr__(self):
        if self.plugin:
            return f"Slot({self.id}, {self.plugin.label})"
        return f"Slot({self.id}, empty)"


class HardwareSlot(Slot):
    """Hardware I/O слот (capture/playback)"""

    def __init__(self, rig: "Rig", slot_id: int, ports: list[str], is_input: bool):
        super().__init__(rig, slot_id)
        self._ports = ports
        self._is_input = is_input

    @property
    def inputs(self) -> list[str]:
        return []

    @property
    def outputs(self) -> list[str]:
        return self._ports if self._is_input else []

    @property
    def hw_inputs(self) -> list[str]:
        """Входи для hardware output slot (playback_1, playback_2)"""
        return self._ports if not self._is_input else []

    @property
    def is_stereo(self) -> bool:
        """Hardware є стерео якщо має 2+ порти"""
        return len(self._ports) >= 2

    @property
    def is_empty(self) -> bool:
        return False

    def load(self, uri: str, x: int = 100, y: int = 200):
        raise NotImplementedError("Cannot load plugin into hardware slot")

    def load_by_name(self, name: str, x: int = 100, y: int = 200):
        raise NotImplementedError("Cannot load plugin into hardware slot")

    def _unload_internal(self):
        pass

    def __repr__(self):
        kind = "Input" if self._is_input else "Output"
        return f"HardwareSlot({self.id}, {kind}, ports={self._ports})"


# =============================================================================
# Rig
# =============================================================================


class Rig:
    """
    Rig — ланцюг ефектів: Input -> [Slot 0] -> [Slot 1] -> ... -> Output
    """

    def __init__(self, config: Config, client: Client = None):
        self.config = config
        self.client = client or Client(config.server.url)

        self.input_slot = HardwareSlot(
            self, -1, ports=config.hardware.inputs, is_input=True
        )

        self.output_slot = HardwareSlot(
            self,
            config.rig.slot_count,
            ports=config.hardware.outputs,
            is_input=False,
        )

        self.slots: list[Slot] = [Slot(self, i) for i in range(config.rig.slot_count)]

        # External callbacks (for UI)
        self._ext_on_param_change: OnParamChangeCallback | None = None
        self._ext_on_bypass_change: OnBypassChangeCallback | None = None
        self._ext_on_structural_change: OnStructuralChangeCallback | None = None

        # Setup WebSocket callbacks
        self.client.ws.set_callbacks(
            on_param_change=self._on_param_change,
            on_bypass_change=self._on_bypass_change,
            on_structural_change=self._on_structural_change,
        )

        # Initial setup - suppress structural callbacks
        self.client.ws.suppress_structural(True)
        try:
            self.client.reset()
            self.reconnect()
        finally:
            self.client.ws.suppress_structural(False)

    def set_callbacks(
        self,
        on_param_change: OnParamChangeCallback | None = None,
        on_bypass_change: OnBypassChangeCallback | None = None,
        on_structural_change: OnStructuralChangeCallback | None = None,
    ):
        """Set external callbacks for UI updates.

        Args:
            on_param_change: Called when parameter changes (label, symbol, value)
            on_bypass_change: Called when bypass changes (label, bypassed)
            on_structural_change: Called when structure changes (msg_type, raw_message)
        """
        self._ext_on_param_change = on_param_change
        self._ext_on_bypass_change = on_bypass_change
        self._ext_on_structural_change = on_structural_change

    # =========================================================================
    # WebSocket event handlers
    # =========================================================================

    def _find_plugin_by_label(self, label: str) -> Plugin | None:
        """Find plugin by its label across all slots."""
        for slot in self.slots:
            if slot.plugin and slot.plugin.label == label:
                return slot.plugin
        return None

    def _on_param_change(self, label: str, symbol: str, value: float):
        """Handle parameter change from WebSocket (update local state)."""
        plugin = self._find_plugin_by_label(label)
        if plugin and symbol in plugin.controls:
            # Update local value without sending back to API
            plugin.controls._controls[symbol].value = value
            # Notify external listener (UI)
            if self._ext_on_param_change:
                self._ext_on_param_change(label, symbol, value)

    def _on_bypass_change(self, label: str, bypassed: bool):
        """Handle bypass change from WebSocket."""
        plugin = self._find_plugin_by_label(label)
        if plugin:
            # Store bypass state on plugin
            plugin._bypassed = bypassed
            # Notify external listener (UI)
            if self._ext_on_bypass_change:
                self._ext_on_bypass_change(label, bypassed)

    def _on_structural_change(self, msg_type: str, raw_message: str):
        """Handle structural change - reset and rebuild rig."""
        print(f"⚠️ Structural change detected: {msg_type} - {raw_message}")
        print("   External change to MOD-UI. Rig state may be out of sync.")
        # Notify external listener (UI)
        if self._ext_on_structural_change:
            self._ext_on_structural_change(msg_type, raw_message)

    def __del__(self):
        self.client.reset()

    @suppress_structural
    def __setitem__(self, key: SupportsIndex, value: str | PluginConfig | None) -> None:
        """
        rig[0] = "http://..."           — завантажити плагін за URI
        rig[0] = "DS1"                  — завантажити плагін за ім'ям з конфігу
        rig[0] = plugin_config          — завантажити плагін з PluginConfig
        rig[0] = None                   — очистити слот
        """
        if value is None:
            self.slots[key]._unload_internal()
        elif isinstance(value, PluginConfig):
            self.slots[key].load(value.uri)
        elif value.startswith("http://") or value.startswith("https://"):
            self.slots[key].load(value)
        else:
            # Припускаємо, що це ім'я плагіна
            self.slots[key].load_by_name(value)
        self.reconnect()

    def __getitem__(self, key: SupportsIndex) -> Slot:
        return self.slots[key]

    def __len__(self) -> int:
        return len(self.slots)

    def reconnect(self):
        """Перебудовує всі з'єднання в ланцюгу"""
        print("\n=== RECONNECT ===")

        chain: list[Slot] = [self.input_slot]
        for slot in self.slots:
            if not slot.is_empty:
                chain.append(slot)
        chain.append(self.output_slot)

        print(f"Active chain: {' -> '.join(repr(s) for s in chain)}")

        self._disconnect_everything()

        for i in range(len(chain) - 1):
            src = chain[i]
            dst = chain[i + 1]
            self._connect_pair(src, dst)

        print("=== RECONNECT DONE ===\n")

    def _connect_pair(self, src: Slot, dst: Slot):
        """З'єднує src -> dst попарно по індексах.

        Логіка по дефолту:
        - Порти з'єднуються по індексу: out[0]->in[0], out[1]->in[1]
        - Якщо виходів більше ніж входів: зайві виходи йдуть на останній вхід
        - Якщо входів більше ніж виходів: останній вихід дублюється на всі зайві входи

        Це дозволяє:
        - mono->mono: out[0]->in[0]
        - mono->stereo: out[0]->in[0], out[0]->in[1]
        - stereo->mono: out[0]->in[0], out[1]->in[0]
        - stereo->stereo: out[0]->in[0], out[1]->in[1]

        Join режим (all-to-all):
        - join_outputs на src: всі виходи з'єднуються з усіма входами
        - join_inputs на dst: всі виходи з'єднуються з усіма входами
        """
        outputs = src.outputs

        if isinstance(dst, HardwareSlot):
            inputs = dst.hw_inputs
        else:
            inputs = dst.inputs

        if not outputs or not inputs:
            print(f"  No connections (outputs={outputs}, inputs={inputs})")
            return

        # Перевіряємо join флаги
        src_config = None
        dst_config = None
        if src.plugin:
            src_config = self.config.get_plugin_by_uri(src.plugin.uri)
        if dst.plugin:
            dst_config = self.config.get_plugin_by_uri(dst.plugin.uri)

        join_outputs = src_config.join_outputs if src_config else False
        join_inputs = dst_config.join_inputs if dst_config else False
        use_join = join_outputs or join_inputs

        connections = []

        if use_join:
            # All-to-all: кожен вихід з'єднується з кожним входом
            for out in outputs:
                for inp in inputs:
                    connections.append((out, inp))
        else:
            # Стандартна логіка: попарно по індексах
            # З'єднуємо кожен вихід з відповідним входом (або останнім якщо входів менше)
            for i, out in enumerate(outputs):
                in_idx = min(i, len(inputs) - 1)
                inp = inputs[in_idx]
                connections.append((out, inp))

            # Якщо входів більше ніж виходів - дублюємо останній вихід
            if len(inputs) > len(outputs):
                last_out = outputs[-1]
                for inp in inputs[len(outputs):]:
                    connections.append((last_out, inp))

        print(f"  Connecting: {connections}")

        for out_path, in_path in connections:
            self.client.effect_connect(out_path, in_path)

    def _disconnect_everything(self):
        """Відключає всі можливі з'єднання"""
        all_outputs = list(self.input_slot.outputs)
        all_inputs = list(self.output_slot.hw_inputs)

        for slot in self.slots:
            all_outputs.extend(slot.outputs)
            all_inputs.extend(slot.inputs)

        for out in all_outputs:
            for inp in all_inputs:
                try:
                    self.client.effect_disconnect(out, inp)
                except Exception:
                    pass

    @suppress_structural
    def clear(self):
        """Очищає всі слоти"""
        for slot in self.slots:
            slot._unload_internal()
        self.reconnect()

    def list_available_plugins(self) -> list[PluginConfig]:
        """Повертає список плагінів з конфігурації"""
        return self.config.plugins

    def list_categories(self) -> list[str]:
        """Повертає список категорій"""
        return self.config.list_categories()

    def get_plugins_by_category(self, category: str) -> list[PluginConfig]:
        """Повертає плагіни певної категорії"""
        return self.config.get_plugins_by_category(category)

    # =========================================================================
    # State Management (Presets)
    # =========================================================================

    def get_state(self) -> dict:
        """Get current rig state as a serializable dict.

        Returns:
            dict with structure:
            {
                "slots": [
                    {"uri": "http://...", "controls": {"Dist": 0.5, ...}, "bypassed": False},
                    None,  # empty slot
                    ...
                ]
            }
        """
        slots_state = []
        for slot in self.slots:
            if slot.plugin:
                slots_state.append({
                    "uri": slot.plugin.uri,
                    "controls": slot.plugin.get_state(),
                    "bypassed": getattr(slot.plugin, "_bypassed", False),
                })
            else:
                slots_state.append(None)

        return {"slots": slots_state}

    @suppress_structural
    def set_state(self, state: dict):
        """Restore rig state from a saved dict.

        Args:
            state: dict from get_state()
        """
        slots_state = state.get("slots", [])

        # First, clear all slots without reconnect
        for slot in self.slots:
            slot._unload_internal()

        # Load plugins into slots
        for i, slot_state in enumerate(slots_state):
            if i >= len(self.slots):
                break

            if slot_state is None:
                continue

            uri = slot_state.get("uri")
            if not uri:
                continue

            try:
                self.slots[i].load(uri)
                # Restore control values
                controls = slot_state.get("controls", {})
                if controls and self.slots[i].plugin:
                    self.slots[i].plugin.set_state(controls)
                # Restore bypass
                bypassed = slot_state.get("bypassed", False)
                if bypassed and self.slots[i].plugin:
                    self.slots[i].plugin.bypass(True)
            except Exception as e:
                print(f"Failed to restore slot {i}: {e}")

        # Reconnect all
        self.reconnect()

    def save_preset(self, filepath: str):
        """Save current rig state to a JSON file.

        Args:
            filepath: Path to save the preset file
        """
        import json
        state = self.get_state()
        with open(filepath, "w") as f:
            json.dump(state, f, indent=2)
        print(f"Preset saved to {filepath}")

    def load_preset(self, filepath: str):
        """Load rig state from a JSON file.

        Args:
            filepath: Path to the preset file
        """
        import json
        with open(filepath, "r") as f:
            state = json.load(f)
        self.set_state(state)
        print(f"Preset loaded from {filepath}")

    def __repr__(self):
        slots_str = ", ".join(
            f"{i}:{s.plugin.name if s.plugin else 'empty'}"
            for i, s in enumerate(self.slots)
        )
        return f"Rig([{slots_str}])"
