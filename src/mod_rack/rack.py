from enum import Enum, auto
import threading
import time
from typing import Any, Callable, SupportsIndex, TypeAlias

import secrets
import string
import weakref

from mod_rack.config import Config, HardwareConfig, PluginConfig
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
OnRackOrderChangeCallback = Callable[
    [list["PluginSlot"]], None
]  # order (list of labels)
OnRackOrderChangeCallbackRef: TypeAlias = (
    weakref.ReferenceType[OnRackOrderChangeCallback]
    | weakref.WeakMethod[OnRackOrderChangeCallback]
)

__all__ = ["PluginSlot", "HardwareSlot", "Rack", "Orchestrator", "OrchestratorMode"]


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

    def __init__(self, ports: list[str], is_input: bool, config: HardwareConfig):
        self.ports = ports
        self._is_input = is_input
        self._join_ports = config.join_inputs if is_input else config.join_outputs

    @property
    def label(self) -> str:
        return "hw_in" if self._is_input else "hw_out"

    @property
    def outputs(self) -> list[str]:
        """Виходи hardware input slot (capture порти)."""
        return self.ports if self._is_input else []

    @property
    def join_outputs(self) -> bool:
        return self._join_ports if self._is_input else False

    @property
    def inputs(self) -> list[str]:
        """Входи hardware output slot (playback порти)."""
        return self.ports if not self._is_input else []

    @property
    def join_inputs(self) -> bool:
        return self._join_ports if not self._is_input else False

    def __repr__(self):
        kind = "Input" if self._is_input else "Output"
        return f"HardwareSlot({kind}, ports={self.ports})"


AnySlot = HardwareSlot | PluginSlot


# =============================================================================
# GridLayoutManager
# =============================================================================


class GridLayoutManager:
    """
    Layout manager for arranging PluginSlots on a grid.

    Attrs:
        X_STEP: Horizontal spacing between plugins.
        Y_STEP: Vertical spacing between rows.
        BASE_X: X coordinate of the first plugin in each row.
        BASE_Y: Y coordinate of the first row.
        Y_THRESHOLD: Maximum Y difference to consider slots in the same row for sorting.
    """

    X_STEP: float = 600.0
    Y_STEP: float = 600.0
    BASE_X: float = 200.0
    BASE_Y: float = 400.0
    Y_THRESHOLD: float = 150.0

    @classmethod
    def sort_slots(cls, slots: list[PluginSlot]) -> list[PluginSlot]:
        """Реюзимо кластеризацію для отримання плаского відсортованого списку."""
        rows = cls.get_clustered_rows(slots)
        # Просто "сплющуємо" список списків у один список
        return [slot for row in rows for slot in row]

    @classmethod
    def normalize(
        cls, slots: list[PluginSlot]
    ) -> dict[PluginSlot, tuple[float, float]]:
        if not slots:
            return {}

        result = {}
        # 1. Отримуємо не просто список, а список кластерів (рядів)
        # Для цього нам треба трохи змінити або використати внутрішню логіку sort_slots
        rows = cls.get_clustered_rows(slots)

        for row_idx, row_slots in enumerate(rows):
            # Кожен кластер отримує свій фіксований Y
            y = cls.BASE_Y + row_idx * cls.Y_STEP

            # Сортуємо плагіни всередині ряду зліва направо
            row_slots.sort(key=lambda s: s.pos_x or 0)

            for col_idx, slot in enumerate(row_slots):
                # Кожен плагін у ряду отримує свій X
                x = cls.BASE_X + col_idx * cls.X_STEP
                result[slot] = (x, y)

        return result

    @classmethod
    def get_clustered_rows(cls, slots: list[PluginSlot]) -> list[list[PluginSlot]]:
        if not slots:
            return []

        # 1. Сортуємо ВСІ слоти спочатку по Y, потім по X, потім по label.
        # Це гарантує детермінованість: при однакових координатах порядок не зміниться.
        active = sorted(
            slots,
            key=lambda s: (
                s.pos_y if s.pos_y is not None else 0,
                s.pos_x if s.pos_x is not None else 0,
                s.label,
            ),
        )

        rows = []
        if not active:
            return rows

        current_row = [active[0]]
        rows.append(current_row)

        # Використовуємо Y першого елемента в ряду як "якір"
        row_anchor_y = active[0].pos_y

        for i in range(1, len(active)):
            slot = active[i]

            # Порівнюємо не з середнім, а з якорем ряду.
            # Додаємо невеликий запас (epsilon), щоб уникнути проблем з float
            if abs(slot.pos_y - row_anchor_y) <= cls.Y_THRESHOLD:
                current_row.append(slot)
            else:
                # Початок нового ряду
                current_row = [slot]
                rows.append(current_row)
                row_anchor_y = slot.pos_y

        # 2. Додатково сортуємо кожен ряд по X, щоб нормалізація не "перемішувала" колонки
        for row in rows:
            row.sort(key=lambda s: (s.pos_x if s.pos_x is not None else 0, s.label))

        return rows

    @classmethod
    def get_insertion_coords(
        cls, slots: list[PluginSlot], index: int | None = None
    ) -> tuple[float, float]:
        """
        Розраховує координати на основі візуальних рядів (кластерів).
        """
        rows = cls.get_clustered_rows(slots)

        # 1. Якщо немає слотів або вставка в самий кінець
        if not rows or index is None or index >= sum(len(r) for r in rows):
            row_idx = len(rows) - 1 if rows else 0
            # Беремо останній ряд
            target_row = rows[-1] if rows else []

            # Якщо останній ряд не порожній, ставимо ПРАВОРУЧ від останнього
            if target_row:
                x = cls.BASE_X + len(target_row) * cls.X_STEP
                y = cls.BASE_Y + (len(rows) - 1) * cls.Y_STEP
            else:
                x, y = cls.BASE_X, cls.BASE_Y

            return (float(x), float(y))

        # 2. Якщо вставка всередині (пошук конкретного ряду та колонки)
        current_idx = 0
        for row_idx, row_slots in enumerate(rows):
            if current_idx <= index < current_idx + len(row_slots):
                col_idx = index - current_idx
                x = cls.BASE_X + col_idx * cls.X_STEP
                y = cls.BASE_Y + row_idx * cls.Y_STEP
                return (float(x), float(y))
            current_idx += len(row_slots)

        return (float(cls.BASE_X), float(cls.BASE_Y))

    @classmethod
    def get_new_row_coords(cls, slots: list[PluginSlot]) -> tuple[float, float]:
        """Повертає координати для початку нового ряду (нижче всіх існуючих)."""
        rows = cls.get_clustered_rows(slots)
        return (float(cls.BASE_X), float(cls.BASE_Y + len(rows) * cls.Y_STEP))


class RoutingManager:
    pass


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


class OrchestratorMode(Enum):
    OBSERVER = auto()  # Тільки дивиться
    MANAGER = auto()  # Вирівнює автоматично


class Orchestrator:
    def __init__(
        self, config: Config, mode: OrchestratorMode = OrchestratorMode.OBSERVER
    ):
        self.mode = mode
        self.config = config
        self.client = Client(config.server.url)

        # Locks and flags
        self._lock = threading.RLock()
        self._loading = True
        self._normalizing = False

        # CallBacks
        self._order_change_listeners: set[OnRackOrderChangeCallbackRef] = set()

        # Slots and connections cache
        self.input_slot = HardwareSlot(ports=[], is_input=True, config=config.hardware)
        self.output_slot = HardwareSlot(
            ports=[], is_input=False, config=config.hardware
        )
        self.slots: list[PluginSlot] = []
        self._connections: set[tuple[str, str]] = set()

        self._subscribe()

    @property
    def normalizing(self):
        return self._normalizing

    @normalizing.setter
    def normalizing(self, value: bool):
        self._normalizing = value

    def run(self):
        self.client.ws.connect()
        while True:
            time.sleep(1)

    def on_rack_order_changed(self, cb: OnRackOrderChangeCallback):
        ref: OnRackOrderChangeCallbackRef
        try:
            ref = weakref.WeakMethod(cb)
        except TypeError:
            ref = weakref.ref(cb)

        with self._lock:
            self._order_change_listeners.add(ref)

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

    def _on_loading_start(self, event: LoadingStartEvent):
        with self._lock:
            if not self._loading:
                _Color.red("\u25a0 Reloading detected")
            _Color.yellow("\u25f7 Loading start, initializing...")
            self._loading = True
            self._normalizing = False
            self._connections.clear()

    def _on_loading_end(self, event: LoadingEndEvent):
        _Color.info("\u25cf Loading end, monitoring...")
        with self._lock:
            self._loading = False
        if not self._loading:
            self._reorder_slots_by_pos(force_emit=True)

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
            if event.name not in slot.ports:
                slot.ports.append(event.name)

    def _on_graph_plugin_add(self, event: GraphPluginAddEvent):
        """
        Handle plugin added via WebSocket feedback.
        Creates Slot, fetches port info, connects to chain.
        """
        _Color.blue(f"+ Plugin: {event.label}")

        # Перевіряємо чи такий слот вже існує
        slot = self.get_slot_by_label(event.label)
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
            self._reorder_slots_by_pos(force_emit=True)

    def _on_graph_plugin_remove(self, event: GraphPluginRemoveEvent):
        """
        Handle plugin removed via WebSocket feedback.
        Updates local graph state ONLY.
        """
        _Color.red(f"- Plugin: {event.label}")

        slot = self.get_slot_by_label(event.label)
        if not slot:
            return

        with self._lock:
            self.slots.remove(slot)
            print(f"  Removed slot: {event.label}")

        if not self._loading:
            self._reorder_slots_by_pos(force_emit=True)

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
        slot = self.get_slot_by_label(event.label)
        if not slot:
            return

        with self._lock:
            # NOTE: we should ensure that plugin already got an update
            x, y = (event.x, event.y)
            if slot.is_pos_changed((x, y)):
                _Color.yellow(f"\u21bb Syncing pos: {slot.label} to {(x, y)}")
                slot.pos_x = x
                slot.pos_y = y

        if not self._loading and not self.normalizing:
            self._reorder_slots_by_pos()

    def _normalize(self, /, force: bool = False):
        if self._loading:
            return

        if self.mode == OrchestratorMode.OBSERVER and not force:
            return

        _Color.info("\u21bb Normalization...")
        new_positions = GridLayoutManager.normalize(self.slots)

        self.normalizing = True

        try:
            for slot, (x, y) in new_positions.items():
                old_pos = (slot.pos_x, slot.pos_y)
                _Color.yellow(f"\u21bb Normalizing: {old_pos} -> {(x, y)}")
                if slot.is_pos_changed((x, y)):
                    slot.pos_x = x
                    slot.pos_y = y
                    # Need small delay before server be able to react
                    time.sleep(0.1)
                    self.client.effect_position(slot.label, x, y)
        except Exception as err:
            _Color.red(f"\u21bb Normalizing: error occured: {err}")
        finally:
            # Debounce delay
            time.sleep(0.4)
            with self._lock:
                self.normalizing = False

    def _reorder_slots_by_pos(self, /, force_emit=False):
        with self._lock:
            # sort slots by pos
            old_order = [s.label for s in self.slots]
            self.slots = GridLayoutManager.sort_slots(self.slots)
            new_order = [s.label for s in self.slots]

        # then normalize
        self._normalize()

        with self._lock:
            # check order changed
            if old_order != new_order or force_emit:
                self._order_changed_emit()

    def _order_changed_emit(self):
        _Color.info("\u21c5 Slots order changed")
        # then if order was changed process callbacks
        dead = set()

        for ref in self._order_change_listeners:
            cb = ref()
            if cb is None:
                dead.add(ref)
            else:
                cb(self.slots)

    def get_slot_by_label(self, label: str) -> PluginSlot | None:
        """Find slot by its plugin label."""
        for slot in self.slots:
            if slot.label == label:
                return slot
        return None


class Rack(Orchestrator):
    """
    Rig — ланцюг ефектів: Input -> [Slot 0] -> [Slot 1] -> ... -> Output

    Реактивна архітектура (Server-as-Source-of-Truth):
    - Клієнт може ініціювати зміни через request_* методи
    - Локальний стан змінюється ТІЛЬКИ у відповідь на WS feedback
    - WS handlers: _on_plugin_added(), _on_plugin_removed()
    """

    def __init__(self, config: Config, client: Client | None = None):
        super().__init__(config=config, mode=OrchestratorMode.MANAGER)

    def _on_loading_end(self, event: LoadingEndEvent):
        super()._on_loading_end(event)
        self.reconnect_seamless()

    def _on_graph_hw_port_add(self, event: GraphAddHwPortEvent):
        super()._on_graph_hw_port_add(event)
        with self._lock:
            if not self._loading:
                self.reconnect_seamless()

    def _on_position_change(self, event: GraphPluginPosEvent):
        """
        Handle position change from WebSocket.
        Updates slot position and reorders slots based on new coordinates.
        """
        super()._on_position_change(event)
        with self._lock:
            if not self._loading:
                self.reconnect_seamless()

    def _on_graph_plugin_add(self, event: GraphPluginAddEvent):
        """
        Handle plugin added via WebSocket feedback.

        Creates Slot, fetches port info, connects to chain.
        """
        super()._on_graph_plugin_add(event)
        with self._lock:
            if not self._loading:
                self.reconnect_seamless()

    def _on_graph_plugin_remove(self, event: GraphPluginRemoveEvent):
        """
        Handle plugin removed via WebSocket feedback.

        Updates local graph state ONLY.
        """
        super()._on_graph_plugin_remove(event)
        with self._lock:
            if not self._loading:
                self.reconnect_seamless()

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
        x, y = GridLayoutManager.get_insertion_coords(self.slots, insert_index)

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
        slot = self.get_slot_by_label(label)
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

    def request_move_slot(self, from_idx: int, to_idx: int):
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

        # slot = self.slots[from_idx]
        # x, y = GridLayoutManager.get_insertion_coords(self.slots, to_idx)
        # self.client.effect_position(slot.label, x, y)

        # Reorder locally
        slot = self.slots.pop(from_idx)
        self.slots.insert(to_idx, slot)

        # Reconnect with new order
        self.reconnect_seamless()

        # Normalize positions (updates server)
        new_positions = GridLayoutManager.normalize(self.slots)
        for slot, (x, y) in new_positions.items():
            slot.pos_x = x
            slot.pos_y = y
            self.client.effect_position(slot.label, x, y)

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

    @classmethod
    def _get_connection_pairs(cls, src: AnySlot, dst: AnySlot) -> list[tuple[str, str]]:
        """Calculate connection pairs between src and dst (without connecting)."""
        outputs = src.outputs
        inputs = dst.inputs

        if not outputs or not inputs:
            return []

        # Check join flags
        join_outputs = False
        join_inputs = False

        if isinstance(src, HardwareSlot):
            join_outputs = src.join_inputs
        elif isinstance(src, PluginSlot) and src.plugin:
            join_outputs = src.plugin.join_outputs

        if isinstance(dst, HardwareSlot):
            join_inputs = dst.join_outputs
        elif isinstance(dst, PluginSlot) and dst.plugin:
            join_inputs = dst.plugin.join_inputs

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

    def get_plugin_by_label(self, label: str) -> Plugin | None:
        """Find plugin by its label."""
        slot = self.get_slot_by_label(label)
        return slot.plugin if slot else None

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
