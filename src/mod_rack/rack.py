import threading
import time
from typing import Any, Callable, SupportsIndex

import secrets
import string
import weakref

from mod_rack.config import Config, PluginConfig
from mod_rack.client import (
    GraphAddHwPortEvent,
    Client,
    GraphConnectEvent,
    GraphDisconnectEvent,
    LoadingEndEvent,
    LoadingStartEvent,
    GraphPluginAddEvent,
    GraphPluginPosEvent,
    GraphPluginRemoveEvent,
)
from mod_rack.plugin import Plugin


# Type aliases for callbacks
OnSlotAddedCallback = Callable[["PluginSlot"], None]  # slot
OnSlotRemovedCallback = Callable[[str], None]  # label
OnOrderChangeCallback = Callable[[list[str]], None]  # order (list of labels)


__all__ = ["PluginSlot", "HardwareSlot", "Rack", "EventMonitor"]


# =============================================================================
# Slots
# =============================================================================


class PluginSlot:
    """
    Слот для плагіна в ланцюгу ефектів.

    Slot завжди містить плагін (немає пустих слотів).
    Slot ідентифікується по label плагіна.
    """

    def __init__(self, plugin: Plugin, pos_x: float = 0, pos_y: float = 0):
        """
        Створює слот з плагіном.

        Args:
            rig: Батьківський Rig
            plugin: Плагін (обов'язковий)
        """
        self.plugin = plugin
        self.pos_x: float = pos_x
        self.pos_y: float = pos_y

    @property
    def label(self) -> str:
        """Унікальний ідентифікатор слота (label плагіна)."""
        return self.plugin.label

    @property
    def inputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.inputs]

    @property
    def outputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.outputs]

    @staticmethod
    def _label_from_uri(uri: str) -> str:
        """Генерує базовий label з URI плагіна."""
        path = uri.split("#")[0].rstrip("/")
        label = path.split("/")[-1]
        return label.replace("#", "_").replace(" ", "_")

    def is_pos_changed(self, new_pos: tuple[float, float]):
        new_x, new_y = new_pos
        old_x = self.pos_x
        old_y = self.pos_y
        return abs(old_x - new_x) >= 1.0 or abs(old_y - new_y) >= 1.0

    def __eq__(self, other):
        if not isinstance(other, PluginSlot):
            return False
        return self.label == other.label

    def __hash__(self):
        return hash(self.label)

    def __repr__(self):
        return f"PluginSlot({self.label})"


class HardwareSlot:
    """Hardware I/O слот (capture/playback)."""

    def __init__(self, ports: list[str], is_input: bool):
        self._ports = ports
        self._is_input = is_input

    @property
    def label(self) -> str:
        return "hw_in" if self._is_input else "hw_out"

    @property
    def outputs(self) -> list[str]:
        """Виходи hardware input slot (capture порти)."""
        return self._ports if self._is_input else []

    @property
    def inputs(self) -> list[str]:
        """Входи hardware output slot (playback порти)."""
        return self._ports if not self._is_input else []

    def __repr__(self):
        kind = "Input" if self._is_input else "Output"
        return f"HardwareSlot({kind}, ports={self._ports})"


AnySlot = HardwareSlot | PluginSlot


# =============================================================================
# GridLayoutManager
# =============================================================================


class GridLayoutManager:
    def __init__(
        self,
        *,
        x_step: int = 600,
        y_step: int = 600,
        base_x: int = 200,
        base_y: int = 400,
        max_per_row: int = 4,
        y_threshold: float = 150.0,
    ):
        """
        Layout manager for arranging PluginSlots on a grid.

        Args:
            x_step: Horizontal spacing between plugins.
            y_step: Vertical spacing between rows.
            base_x: X coordinate of the first plugin in each row.
            base_y: Y coordinate of the first row.
            max_per_row: Maximum number of plugins per row.
            y_threshold: Maximum Y difference to consider slots in the same row for sorting.
        """
        self.x_step = x_step
        self.y_step = y_step
        self.base_x = base_x
        self.base_y = base_y
        self.max_per_row = max_per_row
        self.y_threshold = y_threshold

    def sort_slots(self, slots: list[PluginSlot]) -> list[PluginSlot]:
        """Реюзимо кластеризацію для отримання плаского відсортованого списку."""
        rows = self.get_clustered_rows(slots)
        # Просто "сплющуємо" список списків у один список
        return [slot for row in rows for slot in row]

    def normalize(
        self, slots: list[PluginSlot]
    ) -> dict[PluginSlot, tuple[float, float]]:
        if not slots:
            return {}

        result = {}
        # 1. Отримуємо не просто список, а список кластерів (рядів)
        # Для цього нам треба трохи змінити або використати внутрішню логіку sort_slots
        rows = self.get_clustered_rows(slots)

        for row_idx, row_slots in enumerate(rows):
            # Кожен кластер отримує свій фіксований Y
            y = self.base_y + row_idx * self.y_step

            # Сортуємо плагіни всередині ряду зліва направо
            row_slots.sort(key=lambda s: s.pos_x or 0)

            for col_idx, slot in enumerate(row_slots):
                # Кожен плагін у ряду отримує свій X
                x = self.base_x + col_idx * self.x_step
                result[slot] = (x, y)

        return result

    def get_clustered_rows(self, slots: list[PluginSlot]) -> list[list[PluginSlot]]:
        """Допоміжний метод для отримання групованих за Y слотів."""
        if not slots:
            return []
        active = sorted(
            [s for s in slots if s.pos_y is not None], key=lambda s: s.pos_y
        )

        rows = []
        if active:
            current_row = [active[0]]
            rows.append(current_row)
            for i in range(1, len(active)):
                slot = active[i]
                avg_y = sum(s.pos_y for s in current_row) / len(current_row)
                if abs(slot.pos_y - avg_y) <= self.y_threshold:
                    current_row.append(slot)
                else:
                    current_row = [slot]
                    rows.append(current_row)
        return rows


# =============================================================================
# Rack
# =============================================================================


class _Color:
    @staticmethod
    def info(msg):
        print(f"\033[32m{msg}\033[0m")

    @staticmethod
    def blue(msg):
        print(f"\033[34m{msg}\033[0m")

    @staticmethod
    def red(msg):
        print(f"\033[31m{msg}\033[0m")

    @staticmethod
    def yellow(msg):
        print(f"\033[33m{msg}\033[0m")

    @staticmethod
    def debug(msg):
        print(f"\033[90m{msg}\033[0m")


class EventMonitor:
    def __init__(self, config: Config, *args, **kwargs):
        self.config = config
        self.client = Client(config.server.url)

        # Locks and flags
        self._lock = threading.RLock()
        self._loading = True
        self._normalizing = False

        # CallBacks
        self._order_change_listeners: set[Callable[[Any], None]] = set()
        self._pos_change_listeners: set[Callable[[Any], None]] = set()

        # Slots and connections cache
        self.input_slot = HardwareSlot(ports=[], is_input=True)
        self.output_slot = HardwareSlot(ports=[], is_input=False)
        self.slots: list[PluginSlot] = []
        self._connections: set[tuple[str, str]] = set()

        self._layout_manager = GridLayoutManager()

        self._subscribe()

    @property
    def normalizing(self):
        return self._normalizing

    @normalizing.setter
    def normalizing(self, value: bool):
        self._normalizing = value
        # if not value:
        #     self._reorder_slots_by_pos()

    def run(self):
        self.client.ws.connect()
        while True:
            time.sleep(1)

    def on_order_rig_changed(self, cb: Callable[[Any], None]):
        self._add_listener(self._order_change_listeners, cb)

    def on_pos_change_listeners(self, cb: Callable[[Any], None]):
        self._add_listener(self._pos_change_listeners, cb)

    def _subscribe(self):
        # Setup WebSocket callbacks BEFORE connecting so we don't miss initial messages
        self.client.ws.on(LoadingStartEvent, self._on_loading_start)
        self.client.ws.on(LoadingEndEvent, self._on_loading_end)
        self.client.ws.on(GraphAddHwPortEvent, self._on_graph_hw_port_add)
        self.client.ws.on(GraphPluginAddEvent, self._on_graph_plugin_add)
        self.client.ws.on(GraphPluginRemoveEvent, self._on_graph_plugin_remove)
        self.client.ws.on(GraphConnectEvent, self._on_graph_connect)
        self.client.ws.on(GraphDisconnectEvent, self._on_graph_disconnect)
        self.client.ws.on(GraphPluginPosEvent, self._on_position_change)

    def _add_listener(self, listeners_set, cb):
        try:
            ref = weakref.WeakMethod(cb)
        except TypeError:
            ref = weakref.ref(cb)

        with self._lock:
            listeners_set.add(ref)

    def _on_loading_start(self, event: LoadingStartEvent):
        with self._lock:
            if not self._loading:
                _Color.red("\u25a0 Reloading detected")
            _Color.yellow("\u25f7 Loading start, initializing...")
            self._loading = True
            self._connections.clear()

    def _on_loading_end(self, event: LoadingEndEvent):
        _Color.info("\u25cf Loading end, monitoring...")
        with self._lock:
            self._loading = False
        if not self._loading:
            self._reorder_slots_by_pos()

    def _on_graph_hw_port_add(self, event: GraphAddHwPortEvent):
        """
        Handle hardware port added via WebSocket feedback.
        Updates hardware slots.
        """
        type_ = "Out" if event.is_output else "In"
        _Color.blue(f"+ HW {type_:<3}: {event.name}")
        hw_config = self.config.hardware

        if event.is_output:
            slot = self.output_slot
        else:
            slot = self.input_slot

        if event.name not in hw_config.disable_ports:
            if event.name not in slot._ports:
                slot._ports.append(event.name)

    def _on_graph_plugin_add(self, event: GraphPluginAddEvent):
        """
        Handle plugin added via WebSocket feedback.
        Creates Slot, fetches port info, connects to chain.
        """
        _Color.blue(f"+ Plugin: {event.label}")

        # Перевіряємо чи такий слот вже існує
        slot = self._find_slot_by_label(event.label)
        if not slot:
            # Just update position
            plugin = Plugin.load_supported(
                self.client,
                uri=event.uri,
                label=event.label,
                config=self.config,
            )

            if not plugin:
                _Color.red(f"Can not load plugin: {event.label}, {event.uri}")
                return

            # Створюємо слот
            slot = PluginSlot(
                plugin,
                event.x if event.x is not None else 0,
                event.y if event.y is not None else 0,
            )

        with self._lock:
            # Додаємо слот
            self.slots.append(slot)

        if not self._loading:
            self._reorder_slots_by_pos(force=True)

    def _on_graph_plugin_remove(self, event: GraphPluginRemoveEvent):
        """
        Handle plugin removed via WebSocket feedback.
        Updates local graph state ONLY.
        """
        _Color.red(f"- Plugin: {event.label}")

        slot = self._find_slot_by_label(event.label)
        if not slot:
            return

        with self._lock:
            self.slots.remove(slot)
            print(f"  Removed slot: {event.label}")

        if not self._loading:
            self._reorder_slots_by_pos()

    def _on_graph_connect(self, event: GraphConnectEvent):
        _Color.blue(f"\u221e Connected: {event.src_path} \u21e2 {event.dst_path}")
        with self._lock:
            pair = (event.src_path, event.dst_path)
            if pair not in self._connections:
                self._connections.add(pair)
                print(f"[Cache] Connected: {event.src_path} -> {event.dst_path}")

    def _on_graph_disconnect(self, event: GraphDisconnectEvent):
        _Color.red(f"\u22b6 Disconnected: {event.src_path} \u2307 {event.dst_path}")
        with self._lock:
            pair = (event.src_path, event.dst_path)
            self._connections.discard(pair)
            print(f"[Cache] Disconnected: {event.src_path} -> {event.dst_path}")

    def _on_position_change(self, event: GraphPluginPosEvent):
        _Color.yellow(f"\u2316 Pos: {event.label}, ({event.x}, {event.y})")
        """
        Handle position change from WebSocket.
        Updates slot position and reorders slots based on new coordinates.
        """
        # Skip position updates during normalization (we're sending, not receiving)
        slot = self._find_slot_by_label(event.label)
        if not slot:
            return

        with self._lock:
            # NOTE: we should ensure that plugin already got an update
            old_pos = (slot.pos_x, slot.pos_y)
            x, y = (event.x, event.y)
            if slot.is_pos_changed((x, y)):
                _Color.yellow(f"\u21bb Normalizing: {old_pos} -> {(x, y)}")
                slot.pos_x = x
                slot.pos_y = y

        if not self._loading and not self.normalizing:
            self._reorder_slots_by_pos()

    def _normalize(self):
        _Color.info("\u21bb Normalization...")
        if self._loading:
            return

        new_positions = self._layout_manager.normalize(self.slots)

        self.normalizing = True

        for slot, (x, y) in new_positions.items():
            old_pos = (slot.pos_x, slot.pos_y)
            _Color.yellow(f"\u21bb Normalizing: {old_pos} -> {(x, y)}")
            if slot.is_pos_changed((x, y)):
                slot.pos_x = x
                slot.pos_y = y
                # Need small delay before server be able to react
                time.sleep(0.1)
                self.client.effect_position(slot.label, x, y)

        time.sleep(0.1)
        self.normalizing = False


    def _reorder_slots_by_pos(self, /, force=False):
        with self._lock:
            # sort slots by pos
            old_order = [s.label for s in self.slots]
            self.slots = self._layout_manager.sort_slots(self.slots)
            new_order = [s.label for s in self.slots]

            # then normalize
            self._normalize()

            # check order changed
            if old_order != new_order or force:
                _Color.info("\u21c5 Slots order changed")
                # then if order was changed process callbacks
                dead: set[Callable[[Any], None]] = set()

                for ref in self._order_change_listeners:
                    cb = ref()
                    if cb is None:
                        dead.add(ref)
                    else:
                        cb(None)

    def _find_slot_by_label(self, label: str) -> PluginSlot | None:
        """Find slot by its plugin label."""
        for slot in self.slots:
            if slot.label == label:
                return slot
        return None


class Rack:
    """
    Rig — ланцюг ефектів: Input -> [Slot 0] -> [Slot 1] -> ... -> Output

    Реактивна архітектура (Server-as-Source-of-Truth):
    - Клієнт може ініціювати зміни через request_* методи
    - Локальний стан змінюється ТІЛЬКИ у відповідь на WS feedback
    - WS handlers: _on_plugin_added(), _on_plugin_removed()
    """

    def __init__(
        self, config: Config, client: Client | None = None, prevent_normalization=False
    ):
        self.config = config
        # If caller did not provide a Client, create one but delay WebSocket
        # connection until after callbacks are installed to avoid missing messages.
        if client is None:
            self.client = Client(config.server.url)
        else:
            self.client = client

        self._lock = threading.RLock()

        self._prevent_normalization = prevent_normalization

        self.input_slot = HardwareSlot(ports=[], is_input=True)
        self.output_slot = HardwareSlot(ports=[], is_input=False)

        # Slots list - порядок визначається по координатах (x, y)
        self.slots: list[PluginSlot] = []

        # Кеш активних з'єднань на сервері: {(src_port, dst_port), ...}
        self._connections: set[tuple[str, str]] = set()

        # Flag to defer reconnections during initial pedalboard loading
        self._loading = True

        # External callbacks (for UI)
        self._ext_on_slot_added: OnSlotAddedCallback | None = None
        self._ext_on_slot_removed: OnSlotRemovedCallback | None = None
        self._ext_on_order_change: OnOrderChangeCallback | None = None

        self.layout_manager = GridLayoutManager()

        self._subscribe()
        self.client.ws.connect()

    def _subscribe(self):
        # Setup WebSocket callbacks BEFORE connecting so we don't miss initial messages
        self.client.ws.on(LoadingStartEvent, self._on_loading_start)
        self.client.ws.on(GraphAddHwPortEvent, self._on_graph_hw_port_add)
        self.client.ws.on(GraphPluginAddEvent, self._on_graph_plugin_add)
        self.client.ws.on(GraphPluginRemoveEvent, self._on_graph_plugin_remove)
        self.client.ws.on(GraphConnectEvent, self._on_graph_connect)
        self.client.ws.on(GraphDisconnectEvent, self._on_graph_disconnect)
        self.client.ws.on(GraphPluginPosEvent, self._on_position_change)
        self.client.ws.on(LoadingEndEvent, self._on_loading_end)

    def _on_loading_start(self, event: LoadingStartEvent):
        self._loading = True
        self.slots = []
        self._connections.clear()

    def _on_loading_end(self, event: LoadingEndEvent):
        self._loading = False

        # Використовуємо LayoutManager
        new_positions = self.layout_manager.normalize(self.slots)
        for slot, (x, y) in new_positions.items():
            slot.pos_x = x
            slot.pos_y = y
            self.client.effect_position(slot.label, x, y)

        self.reconnect_seamless()

    def _on_graph_hw_port_add(self, event: GraphAddHwPortEvent):
        hw_config = self.config.hardware

        if event.is_output:
            slot = self.output_slot
        else:
            slot = self.input_slot

        if event.name not in hw_config.disable_ports:
            if event.name not in slot._ports:
                slot._ports.append(event.name)

    def _on_graph_connect(self, event: GraphConnectEvent):
        with self._lock:
            pair = (event.src_path, event.dst_path)
            if pair not in self._connections:
                self._connections.add(pair)
                print(f"  [Cache] Connected: {event.src_path} -> {event.dst_path}")

    def _on_graph_disconnect(self, event: GraphDisconnectEvent):
        with self._lock:
            pair = (event.src_path, event.dst_path)
            self._connections.discard(pair)
            print(f"  [Cache] Disconnected: {event.src_path} -> {event.dst_path}")

    def set_callbacks(
        self,
        on_slot_added: OnSlotAddedCallback | None = None,
        on_slot_removed: OnSlotRemovedCallback | None = None,
        on_order_change: OnOrderChangeCallback | None = None,
    ):
        """Set external callbacks for UI updates."""
        self._ext_on_slot_added = on_slot_added
        self._ext_on_slot_removed = on_slot_removed
        self._ext_on_order_change = on_order_change

    # =========================================================================
    # WebSocket event handlers (Server-as-Source-of-Truth)
    # =========================================================================

    def _find_slot_by_label(self, label: str) -> PluginSlot | None:
        """Find slot by its plugin label."""
        for slot in self.slots:
            if slot.label == label:
                return slot
        return None

    def _find_plugin_by_label(self, label: str) -> Plugin | None:
        """Find plugin by its label."""
        slot = self._find_slot_by_label(label)
        return slot.plugin if slot else None

    def _on_position_change(self, event: GraphPluginPosEvent):
        """Handle position change from WebSocket.

        Updates slot position and reorders slots based on new coordinates.
        """
        # Skip position updates during normalization (we're sending, not receiving)
        with self._lock:
            slot = self._find_slot_by_label(event.label)
            if not slot:
                print(f"  Position change for unknown slot {event.label}, ignoring")
                return

            # NOTE: we should ensure that plugin already got an update
            slot.pos_x = event.x
            slot.pos_y = event.y

            # Skip reordering during initialization
            if self._loading:
                return

            # Reorder slots based on new positions and reconnect if order changed
            self._reorder_by_layout()

    def _reorder_by_layout(self):
        old_order = [s.label for s in self.slots]
        self.slots = self.layout_manager.sort_slots(self.slots)
        new_order = [s.label for s in self.slots]

        if old_order != new_order:
            self.reconnect_seamless()
            if self._ext_on_order_change:
                self._ext_on_order_change(new_order)

        self._apply_normalization()

    def _apply_normalization(self):
        if self._prevent_normalization:
            return

        with self._lock:
            updates = self.layout_manager.normalize(self.slots)
            for slot, (x, y) in updates.items():
                if abs(slot.pos_x - x) > 10 or abs(slot.pos_y - y) > 10:
                    slot.pos_x = x
                    slot.pos_y = y
                    self.client.effect_position(slot.label, x, y)

    def _on_graph_plugin_add(self, event: GraphPluginAddEvent):
        """
        Handle plugin added via WebSocket feedback.

        Creates Slot, fetches port info, connects to chain.
        """
        # Перевіряємо чи такий слот вже існує
        slot = self._find_slot_by_label(event.label)
        if not slot:
            # Just update position
            plugin = Plugin.load_supported(
                self.client,
                uri=event.uri,
                label=event.label,
                config=self.config,
            )

            if not plugin:
                print(f"Can not load plugin: {event.label}, {event.uri}")
                return

            # Створюємо слот
            slot = PluginSlot(
                plugin,
                event.x if event.x is not None else 0,
                event.y if event.y is not None else 0,
            )

        with self._lock:
            # Додаємо слот
            self.slots.append(slot)

            print(
                f"  Created slot: {slot} at index {self.slots.index(slot)} (pos: {event.x}, {event.y})"
            )

            # Сортуємо
            self.slots = self.layout_manager.sort_slots(self.slots)

            # Сповіщуємо UI
            if self._ext_on_slot_added:
                self._ext_on_slot_added(slot)

            # Нормалізуємо тільки після завантаження
            if not self._loading:
                new_positions = self.layout_manager.normalize(self.slots)
                for slot, (x, y) in new_positions.items():
                    slot.pos_x = x
                    slot.pos_y = y
                    self.client.effect_position(slot.label, x, y)
                # Connect into chain UNLESS we're still initializing
                self.reconnect_seamless()

    def _on_graph_plugin_remove(self, event: GraphPluginRemoveEvent):
        """
        Handle plugin removed via WebSocket feedback.

        Updates local graph state ONLY.
        """
        slot = self._find_slot_by_label(event.label)
        if not slot:
            print(f"  Slot {event.label} not found, skipping")
            return

        with self._lock:
            self.slots.remove(slot)
            print(f"  Removed slot: {event.label}")

        # Normalize remaining positions to fill the gap
        if not self._loading:
            new_positions = self.layout_manager.normalize(self.slots)
            for slot, (x, y) in new_positions.items():
                slot.pos_x = x
                slot.pos_y = y
                self.client.effect_position(slot.label, x, y)

        # Notify UI
        if self._ext_on_slot_removed:
            self._ext_on_slot_removed(event.label)

    # =========================================================================
    # Request API (ініціювання без локальних змін)
    # =========================================================================

    @staticmethod
    def _generate_label(uri: str) -> str:
        """Generate unique label for plugin."""
        base = PluginSlot._label_from_uri(uri)
        alphabet = string.ascii_letters + string.digits
        uid = "".join(secrets.choice(alphabet) for _ in range(8))
        return f"{base}_{uid}"

    def request_add_plugin(self, uri: str, x: int = 500, y: int = 400) -> str | None:
        """
        Request to add plugin via REST.

        Does NOT create local slot - waits for WS feedback.

        Args:
            uri: Plugin URI
            x, y: Position on MOD-UI

        Returns:
            label якщо REST OK, None якщо помилка
        """
        plugin_config = self.config.get_plugin_by_uri(uri)
        if not plugin_config:
            print(f"Plugin not supported: {uri}")
            return None

        label = self._generate_label(uri)

        result = self.client.effect_add(label, uri, x, y)

        if not result or not isinstance(result, dict) or not result.get("valid"):
            print(f"REST error: Failed to add plugin {uri}")
            return None

        print(f"REST OK: Requested add {label}, waiting for WS feedback")
        return label

    def request_add_plugin_at(self, uri: str, insert_index: int) -> str | None:
        """
        Request to add plugin at a specific chain position.

        Calculates the appropriate X,Y coordinates based on the target index,
        so the plugin will be sorted into the correct position.

        Args:
            uri: Plugin URI
            insert_index: Target position in the chain

        Returns:
            label if REST OK, None if error
        """
        # Calculate position for this index
        x, y = self._calculate_position_for_index(insert_index)

        return self.request_add_plugin(uri, x=int(x), y=int(y))

    def request_remove_plugin(self, label: str) -> bool:
        """
        Request plugin removal via REST with pre-connect of neighbors.

        Steps:
        1. Find plugin and its neighbors.
        2. Pre-connect neighbors to keep audio seamless.
        3. Call effect_remove.
        4. If remove fails, fallback to seamless_reconnect.

        Args:
            label: Plugin label

        Returns:
            True if remove requested successfully, False otherwise
        """
        slot = self._find_slot_by_label(label)
        if not slot:
            print(f"Plugin {label} not found locally, cannot remove")
            return False

        idx = self.slots.index(slot)

        # Find neighbors
        src: AnySlot = self.input_slot
        for s in self.slots[:idx]:
            src = s

        dst: AnySlot = self.output_slot
        for s in self.slots[idx + 1 :]:
            dst = s
            break

        # Pre-connect neighbors
        if src and dst:
            print(f"Pre-connecting neighbors before removal: {src} -> {dst}")
            self._connect_pair(src, dst)

        # Attempt removal
        success = self.client.effect_remove(label)
        if not success:
            print(f"REST remove failed for {label}, doing seamless reconnect")
        else:
            print(f"REST OK: Requested remove {label}, waiting for WS feedback")

        return success

    def move_slot(self, from_idx: int, to_idx: int):
        """
        Move slot to different position in chain.

        Reorders locally, reconnects, and normalizes positions on server.

        Args:
            from_idx: Current position
            to_idx: New position
        """
        if from_idx < 0 or from_idx >= len(self.slots):
            return
        if to_idx < 0 or to_idx >= len(self.slots):
            return
        if from_idx == to_idx:
            return

        print(f"Moving slot from idx {from_idx} to {to_idx}")

        # Reorder locally
        slot = self.slots.pop(from_idx)
        self.slots.insert(to_idx, slot)

        # Reconnect with new order
        self.reconnect_seamless()

        # Normalize positions (updates server)
        new_positions = self.layout_manager.normalize(self.slots)
        for slot, (x, y) in new_positions.items():
            slot.pos_x = x
            slot.pos_y = y
            self.client.effect_position(slot.label, x, y)

        # Notify UI
        if self._ext_on_order_change:
            self._ext_on_order_change([s.label for s in self.slots])

    def _calculate_position_for_index(
        self,
        target_idx: int,
        exclude_slot: PluginSlot | None = None,
    ) -> tuple[float, float]:
        """
        Calculate X,Y position for inserting a slot at target index.

        Args:
            target_idx: Target position in the chain
            exclude_slot: Slot to exclude from calculations (the one being moved)

        Returns:
            (x, y) coordinates
        """
        layout = self.layout  # GridLayoutManager

        # Список інших слотів без exclude_slot
        other_slots = [s for s in self.slots if s != exclude_slot]

        # Якщо нема інших слотів, вставляємо в базову позицію
        if not other_slots:
            return (layout.base_x, layout.base_y)

        # Сортуємо слоти по Y-кластерах та X
        sorted_slots = layout.sort_slots(other_slots)

        # Вставка перед першим
        if target_idx <= 0:
            first = sorted_slots[0]
            return (first.pos_x - layout.x_step, first.pos_y)

        # Вставка після останнього
        if target_idx >= len(sorted_slots):
            last = sorted_slots[-1]
            return (last.pos_x + layout.x_step, last.pos_y)

        # Вставка між двома слотами
        prev_slot = sorted_slots[target_idx - 1]
        next_slot = sorted_slots[target_idx]

        # Ряд обчислюємо по Y базовим кроком
        row = (target_idx - 1) // layout.max_per_row
        new_y = layout.base_y + row * layout.y_step

        # X розташовуємо посередині або мінімум x_step від prev
        new_x = max(
            prev_slot.pos_x + layout.x_step, (prev_slot.pos_x + next_slot.pos_x) / 2
        )

        return (new_x, new_y)

    # =========================================================================
    # Routing
    # =========================================================================

    def reconnect(self):
        """Rebuild all connections in the chain (break-before-make, causes audio gap)."""
        print("\n=== RECONNECT ===")

        chain = [self.input_slot] + self.slots + [self.output_slot]
        print(f"Chain: {' -> '.join(repr(s) for s in chain)}")

        self._disconnect_everything()

        for i in range(len(chain) - 1):
            self._connect_pair(chain[i], chain[i + 1])

        print("=== RECONNECT DONE ===\n")

    def reconnect_seamless(self):
        if self._loading:
            return

        with self._lock:
            chain = [self.input_slot] + self.slots + [self.output_slot]

            desired = set()
            for i in range(len(chain) - 1):
                desired.update(self._get_connection_pairs(chain[i], chain[i + 1]))

            # Ключовий момент: to_connect — це те, чого реально немає в кеші
            to_connect = desired - self._connections
            # to_disconnect — це те, що є в кеші, але більше не потрібно
            to_disconnect = self._connections - desired

            if not to_connect and not to_disconnect:
                return  # Нічого не змінилося

            print("--- Syncing Graph ---")
            for out_p, in_p in to_connect:
                print(f"  [+] Connecting: {out_p} -> {in_p}")
                self.client.effect_connect(out_p, in_p)

            for out_p, in_p in to_disconnect:
                print(f"  [-] Disconnecting: {out_p} -> {in_p}")
                self.client.effect_disconnect(out_p, in_p)

        print("=== RECONNECT SEAMLESS DONE ===\n")

    def _get_connection_pairs(
        self, src: AnySlot, dst: AnySlot
    ) -> list[tuple[str, str]]:
        """Calculate connection pairs between src and dst (without connecting)."""
        outputs = src.outputs
        inputs = dst.inputs

        if not outputs or not inputs:
            return []

        # Check join flags
        join_outputs = False
        join_inputs = False

        if isinstance(src, HardwareSlot):
            join_outputs = self.config.hardware.join_inputs
        elif isinstance(src, PluginSlot) and src.plugin:
            src_config = self.config.get_plugin_by_uri(src.plugin.uri)
            join_outputs = src_config.join_outputs if src_config else False

        if isinstance(dst, HardwareSlot):
            join_inputs = self.config.hardware.join_outputs
        elif isinstance(dst, PluginSlot) and dst.plugin:
            dst_config = self.config.get_plugin_by_uri(dst.plugin.uri)
            join_inputs = dst_config.join_inputs if dst_config else False

        connections = []

        if join_outputs or join_inputs:
            # All-to-all
            for out in outputs:
                for inp in inputs:
                    connections.append((out, inp))
        else:
            # Pair by index
            for i, out in enumerate(outputs):
                in_idx = min(i, len(inputs) - 1)
                connections.append((out, inputs[in_idx]))

            if len(inputs) > len(outputs):
                last_out = outputs[-1]
                for inp in inputs[len(outputs) :]:
                    connections.append((last_out, inp))

        return connections

    def _reconnect_slot(self, slot: PluginSlot):
        """
        Connect a slot into the chain (make-before-break).

        1. Connect new slot into chain
        2. Disconnect old direct path
        """
        slot_idx = self.slots.index(slot)
        if slot_idx < 0:
            return

        # Find neighbors
        src = self.input_slot if slot_idx == 0 else self.slots[slot_idx - 1]
        dst = (
            self.output_slot
            if slot_idx == len(self.slots) - 1
            else self.slots[slot_idx + 1]
        )

        # Connect new path
        print(f"  Connect: {src} -> {slot}")
        self._connect_pair(src, slot)

        print(f"  Connect: {slot} -> {dst}")
        self._connect_pair(slot, dst)

        # Disconnect old direct path
        print(f"  Disconnect: {src} -> {dst}")
        self._disconnect_pair(src, dst)

    def _connect_pair(self, src: AnySlot, dst: AnySlot):
        """Connect src outputs to dst inputs."""
        connections = self._get_connection_pairs(src, dst)
        if not connections:
            return

        print(f"    Connecting: {connections}")
        for out_path, in_path in connections:
            self.client.effect_connect(out_path, in_path)

    def _disconnect_pair(self, src: AnySlot, dst: AnySlot):
        """Disconnect connections between src and dst."""
        outputs = src.outputs
        inputs = dst.inputs

        for out in outputs:
            for inp in inputs:
                try:
                    self.client.effect_disconnect(out, inp)
                except Exception:
                    pass

    def _disconnect_everything(self):
        """
        Видаляє всі активні з'єднання, про які знає локальний кеш.
        Використовує O(N) операцій замість перебору всіх портів.
        """
        with self._lock:
            if not self._connections:
                print("  No active connections to disconnect.")
                return

            print(f"  Disconnecting everything: {len(self._connections)} connections")

            # Робимо копію списку для ітерації, оскільки WS-події
            # можуть спробувати змінити set через discard()
            current_pairs = list(self._connections)

            for out_path, in_path in current_pairs:
                try:
                    # Ми не видаляємо з self._connections вручну,
                    # це зробить _on_graph_disconnect, коли прийде відповідь від сервера.
                    self.client.effect_disconnect(out_path, in_path)
                except Exception as e:
                    print(f"    Error disconnecting {out_path} -> {in_path}: {e}")

    # =========================================================================
    # Convenience API
    # =========================================================================

    def __getitem__(self, key: SupportsIndex) -> PluginSlot:
        return self.slots[key]

    def __len__(self) -> int:
        return len(self.slots)

    def get_slot_by_label(self, label: str) -> PluginSlot | None:
        """Find slot by label."""
        return self._find_slot_by_label(label)

    def list_supported_plugins(self) -> list[PluginConfig]:
        """List plugins from config."""
        return self.config.plugins

    def list_categories(self) -> list[str]:
        """List plugin categories."""
        return self.config.list_categories()

    def get_plugins_by_category(self, category: str) -> list[PluginConfig]:
        """Get plugins by category."""
        return self.config.get_plugins_by_category(category)

    def clear(self):
        """Request removal of all plugins."""
        for slot in list(self.slots):
            self.request_remove_plugin(slot.label)

    def __repr__(self):
        slots_str = ", ".join(f"{i}:{s.label}" for i, s in enumerate(self.slots))
        return f"Rig([{slots_str}])"
