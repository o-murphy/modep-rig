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
    GraphRemoveHwPortEvent,
    PortDirection,
    PortType,
    RemoveAllEvent,
    LoadingEndEvent,
    LoadingStartEvent,
    GraphPluginAddEvent,
    GraphPluginPosEvent,
    GraphPluginRemoveEvent,
    ResetConnectionsEvent,
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

__all__ = [
    "PluginSlot",
    "HardwareSlot",
    "Rack",
    "Orchestrator",
    "OrchestratorMode",
    "GridLayoutManager",
    "RoutingManager",
]


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
    def audio_inputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.audio_inputs]

    @property
    def audio_outputs(self) -> list[str]:
        return [p.graph_path for p in self.plugin.audio_outputs]

    @property
    def join_audio_outputs(self) -> bool:
        return self.plugin.join_audio_outputs

    @property
    def join_audio_inputs(self) -> bool:
        return self.plugin.join_audio_inputs

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

    def __init__(
        self, audio_ports: list[str], direction: PortDirection, config: HardwareConfig
    ):
        # Використовуємо звичайні атрибути, щоб вони були доступні відразу
        self.audio_ports = audio_ports  # список імен портів
        self.direction = direction

        # Налаштування з конфігу
        if direction == PortDirection.INPUT:
            self.join_audio_ports = config.join_audio_inputs
            self.label = "hw_in"
        else:
            self.join_audio_ports = config.join_audio_outputs
            self.label = "hw_out"

    @property
    def audio_inputs(self) -> list[str]:
        """
        Для Hardware INPUT (capture) - це джерело сигналу.
        Але в термінах графа MOD вони поводяться як ВИХОДИ (outputs).
        Тому для сумісності з RoutingManager:
        """
        # Якщо це вхідний залізячний слот, він НЕ МАЄ входів у графі (він - початок)
        return [] if self.direction == PortDirection.INPUT else self.audio_ports

    @property
    def audio_outputs(self) -> list[str]:
        """Це те, що йде В ГРАФ."""
        # Якщо це вхідний залізячний слот, його порти є ВИХОДАМИ для графа
        return self.audio_ports if self.direction == PortDirection.INPUT else []

    @property
    def join_audio_inputs(self) -> bool:
        return (
            self.join_audio_ports if self.direction == PortDirection.OUTPUT else False
        )

    @property
    def join_audio_outputs(self) -> bool:
        return self.join_audio_ports if self.direction == PortDirection.INPUT else False

    def __repr__(self):
        return f"HardwareSlot({self.direction.name}, ports={self.audio_ports})"


AnySlot = HardwareSlot | PluginSlot


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

    X_STEP: float = 1000.0
    Y_STEP: float = 600.0
    BASE_X: float = 200.0
    BASE_Y: float = 200.0
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
    def move_slot(
        cls, slots: list[PluginSlot], from_idx: int, to_idx: int
    ) -> dict[PluginSlot, tuple[float, float]]:
        """
        Переміщує слот у списку та перепризначає існуючі координати слотам
        відповідно до їхнього нового порядку.
        """
        if not slots:
            return {}

        # 1. Отримуємо стабільно відсортований поточний список
        ordered_slots = cls.sort_slots(list(slots))

        # 2. Зберігаємо всі поточні координати у тому порядку, в якому вони є зараз
        # Це наш "шаблон" позицій на екрані
        coords_template = [(s.pos_x, s.pos_y) for s in ordered_slots]

        if from_idx < 0 or from_idx >= len(ordered_slots):
            return {s: (s.pos_x, s.pos_y) for s in slots}

        # 3. Виконуємо перестановку об'єктів у списку
        to_idx = max(0, min(to_idx, len(ordered_slots) - 1))
        slot_to_move = ordered_slots.pop(from_idx)
        ordered_slots.insert(to_idx, slot_to_move)

        # 4. Створюємо ret_val: беремо переставлені слоти
        # і даємо їм координати з шаблону по порядку
        result = {}
        for idx, slot in enumerate(ordered_slots):
            new_x, new_y = coords_template[idx]
            result[slot] = (float(new_x), float(new_y))

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

        rows: list[list[PluginSlot]] = []
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


# =============================================================================
# GridLayoutManager
# =============================================================================


class RoutingManager:
    """
    Stateless manager для розрахунку з'єднань у графі.
    Не керує станом, лише повертає набори портів.
    """

    @classmethod
    def get_audio_connection_pairs(cls, src: AnySlot, dst: AnySlot) -> list[tuple[str, str]]:
        """Розраховує пари (вихід, вхід) між двома слотами."""
        outputs = src.audio_outputs
        inputs = dst.audio_inputs

        print(src, outputs, dst, inputs)

        if not outputs or not inputs:
            return []

        # Визначаємо прапори об'єднання (join)
        join_audio_outputs = src.join_audio_outputs
        join_audio_inputs = dst.join_audio_inputs

        connections = []

        if join_audio_outputs or join_audio_inputs:
            # All-to-all: кожен вихід з кожним входом
            for out in outputs:
                for inp in inputs:
                    connections.append((out, inp))
        else:
            # Pair-by-index: 1-1, 2-2, а надлишок до останнього
            for i, out in enumerate(outputs):
                in_idx = min(i, len(inputs) - 1)
                connections.append((out, inputs[in_idx]))

            if len(inputs) > len(outputs):
                last_out = outputs[-1]
                for inp in inputs[len(outputs) :]:
                    connections.append((last_out, inp))

        return connections

    @classmethod
    def calculate_audio_chain_connections(
        cls,
        slots: list[PluginSlot],
        input_slot: HardwareSlot,
        output_slot: HardwareSlot,
    ) -> set[tuple[str, str]]:
        """Повертає повний набір бажаних з'єднань для всього ланцюга."""
        desired = set()
        chain = [input_slot] + slots + [output_slot]

        for i in range(len(chain) - 1):
            pairs = cls.get_audio_connection_pairs(chain[i], chain[i + 1])
            desired.update(pairs)

        return desired


# =============================================================================
# Orchestrator
# =============================================================================


class OrchestratorMode(Enum):
    OBSERVER = auto()  # Тільки дивиться
    MANAGER = auto()  # Вирівнює автоматично


class Orchestrator:
    def __init__(
        self, config: Config, mode: OrchestratorMode = OrchestratorMode.MANAGER
    ):
        self.mode = mode
        self.config = config
        self.client = Client(config.server.url)

        # Locks and flags
        self._lock = threading.RLock()
        self._reorder_timer: threading.Timer | None = None
        self._debounce_delay: float = 0.1
        self._loading = True
        self._normalizing = False

        # CallBacks
        self._order_change_listeners: set[OnRackOrderChangeCallbackRef] = set()

        # Slots and connections cache
        self.input_slot = HardwareSlot(
            audio_ports=[], direction=PortDirection.INPUT, config=config.hardware
        )
        self.output_slot = HardwareSlot(
            audio_ports=[], direction=PortDirection.OUTPUT, config=config.hardware
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
        self.client.ws.on(GraphRemoveHwPortEvent, self._on_graph_hw_port_remove)
        self.client.ws.on(GraphPluginAddEvent, self._on_graph_plugin_add)
        self.client.ws.on(GraphPluginRemoveEvent, self._on_graph_plugin_remove)
        self.client.ws.on(RemoveAllEvent, self._on_remove_all)
        self.client.ws.on(GraphConnectEvent, self._on_graph_connect)
        self.client.ws.on(GraphDisconnectEvent, self._on_graph_disconnect)
        self.client.ws.on(ResetConnectionsEvent, self._on_reset_connections)
        self.client.ws.on(GraphPluginPosEvent, self._on_position_change)

    def _on_loading_start(self, event: LoadingStartEvent):
        with self._lock:
            if not self._loading:
                _Color.red("\u25a0 Reloading detected")
            _Color.yellow("\u25f7 Loading start, initializing...")
            self._loading = True
            self._normalizing = False
            self.slots.clear()
            self._connections.clear()

    def _on_loading_end(self, event: LoadingEndEvent):
        _Color.info("\u25cf Loading end, monitoring...")
        with self._lock:
            self._loading = False
        if not self._loading:
            self._schedule_reorder(force_emit=True)

    def _on_reset_connections(self, event: ResetConnectionsEvent | Any):
        with self._lock:
            self._connections.clear()
            self._schedule_reorder()

    def _on_remove_all(self, event: RemoveAllEvent | Any):
        with self._lock:
            self.slots.clear()
            self._schedule_reorder()

    def _on_graph_hw_port_add(self, event: GraphAddHwPortEvent):
        """
        Handle hardware port added via WebSocket feedback.
        Updates hardware slots.
        """
        _Color.debug(
            f"~ HW {event.port_type.name} {event.direction.name}: {event.name}"
        )

        if event.port_type == PortType.AUDIO:
            hw_config = self.config.hardware

            if event.direction == PortDirection.OUTPUT:
                slot = self.output_slot
            else:
                slot = self.input_slot

            if event.name not in hw_config.disable_ports:
                if event.name not in slot.audio_ports:
                    slot.audio_ports.append(event.name)
                    _Color.blue(
                        f"+ HW {event.port_type.name} {event.direction.name}: {event.name}"
                    )

        elif event.port_type == PortType.MIDI:
            _Color.red("Warning: HW MIDI PORTS IS NOT YET SUPPORTED")

        self._schedule_reorder()

    def _on_graph_hw_port_remove(self, event: GraphRemoveHwPortEvent):
        # TODO: handle this event
        self._schedule_reorder()

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
                # TODO: maybe should remove it
                # with self._lock:
                #     self.client.effect_remove(event.label)
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
            self._schedule_reorder(force_emit=True)

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
            self._schedule_reorder(force_emit=True)

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
            x, y = (event.x, event.y)
            if slot.is_pos_changed((x, y)):
                _Color.yellow(f"\u21bb Syncing pos: {slot.label} to {(x, y)}")
                slot.pos_x = x
                slot.pos_y = y

        if not self._loading and not self.normalizing:
            self._schedule_reorder()

    def _normalize_layout(self, /, force: bool = False):
        if self._loading:
            return

        if self.mode != OrchestratorMode.MANAGER and not force:
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
                    self.client.effect_position(slot.label, x, y)
        except Exception as err:
            _Color.red(f"\u21bb Normalizing: error occured: {err}")
        finally:
            with self._lock:
                self.normalizing = False

    def _reorder_slots_by_pos(self, /, force_emit=False):
        with self._lock:
            # sort slots by pos
            old_order = [s.label for s in self.slots]
            self.slots = GridLayoutManager.sort_slots(self.slots)
            new_order = [s.label for s in self.slots]

        # then normalize
        self._normalize_layout()

        with self._lock:
            # check order changed
            if old_order != new_order or force_emit or (not self.slots and old_order):
                self.reconnect_seamless()
                self._order_changed_emit()

    def _schedule_reorder(self, /, force_emit: bool = False):
        if self._reorder_timer:
            self._reorder_timer.cancel()

        # Таймер викличе реордер в окремому потоці через 200мс спокою
        self._reorder_timer = threading.Timer(
            self._debounce_delay,
            self._reorder_slots_by_pos,
            kwargs={"force_emit": force_emit},
        )

        self._reorder_timer.start()

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

    # =========================================================================
    # Routing
    # =========================================================================

    def reconnect_seamless(self):
        """Синхронізує поточні з'єднання на сервері з розрахованим ідеалом."""
        if self._loading:
            return

        if self.mode != OrchestratorMode.MANAGER:
            return

        with self._lock:
            # 1. Отримуємо "ідеальний" стан від менеджера
            desired = RoutingManager.calculate_audio_chain_connections(
                self.slots, self.input_slot, self.output_slot
            )
            print(desired)

            # 2. Обчислюємо різницю з кешем Orchestrator
            to_connect = desired - self._connections
            to_disconnect = self._connections - desired

            if not to_connect and not to_disconnect:
                return

            _Color.info("--- Syncing Graph ---")
            for out_p, in_p in to_connect:
                self.client.effect_connect(out_p, in_p)

            for out_p, in_p in to_disconnect:
                self.client.effect_disconnect(out_p, in_p)

        print("=== RECONNECT SEAMLESS DONE ===\n")

    def _connect_pair(self, src: AnySlot, dst: AnySlot):
        """Проксі-метод для точкового з'єднання (наприклад, при видаленні плагіна)."""
        pairs = RoutingManager.get_audio_connection_pairs(src, dst)
        for out_path, in_path in pairs:
            # Важливо: ми не додаємо в self._connections самі, чекаємо WS події
            self.client.effect_connect(out_path, in_path)

    def _disconnect_everything(self):
        """Видаляє всі активні з'єднання, базуючись на актуальному кеші."""
        with self._lock:
            if not self._connections:
                return

            print(f"  Disconnecting everything: {len(self._connections)} connections")

            # Копіюємо для ітерації
            current_pairs = list(self._connections)
            for out_path, in_path in current_pairs:
                try:
                    self.client.effect_disconnect(out_path, in_path)
                except Exception as e:
                    _Color.red(f"Error disconnecting {out_path} -> {in_path}: {e}")

    def clear(self):
        """Request removal of all plugins safely."""
        with self._lock:
            if not self.slots:
                return

            _Color.info("--- Clearing Rack ---")

            # 1. Зупиняємо моніторинг порядку на час масового видалення
            # (необов'язково, але корисно мати прапор масової операції)

            # 2. Розірвати всі кабелі одним махом, щоб не було тріску
            # при перепідключенні сусідів, які теж зараз зникнуть
            self._disconnect_everything()

            # 3. Видаляємо плагіни.
            # Використовуємо прямий виклик client, щоб уникнути
            # зайвої логіки "сусідів" у request_remove_plugin
            labels_to_remove = [slot.label for slot in self.slots]

            for label in labels_to_remove:
                self.client.effect_remove(label)

            self.client.reset()

            # 4. Примусово оновлюємо стан, якщо хочемо миттєвої реакції
            # Хоча WS-події прийдуть і самі запустять реордер.


# =============================================================================
# Rack
# =============================================================================


class Rack(Orchestrator):
    """
    Rig — ланцюг ефектів: Input -> [Slot 0] -> [Slot 1] -> ... -> Output

    Реактивна архітектура (Server-as-Source-of-Truth):
    - Клієнт може ініціювати зміни через request_* методи
    - Локальний стан змінюється ТІЛЬКИ у відповідь на WS feedback
    - WS handlers: _on_plugin_added(), _on_plugin_removed()
    """

    def __init__(
        self, config: Config, mode: OrchestratorMode = OrchestratorMode.MANAGER
    ):
        super().__init__(config=config, mode=mode)

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

        if self._loading:
            return

        _Color.info("\u21c5 Reordering...")
        new_positions = GridLayoutManager.move_slot(self.slots, from_idx, to_idx)

        self.normalizing = True

        try:
            for slot, (x, y) in new_positions.items():
                old_pos = (slot.pos_x, slot.pos_y)
                _Color.yellow(f"\u21c5 Reordering: {old_pos} -> {(x, y)}")
                if slot.is_pos_changed((x, y)):
                    slot.pos_x = x
                    slot.pos_y = y
                    self.client.effect_position(slot.label, x, y)
        except Exception as err:
            _Color.red(f"\u21bb Normalizing: error occured: {err}")
        finally:
            with self._lock:
                self.normalizing = False
            self._schedule_reorder(force_emit=True)

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

    def __repr__(self):
        slots_str = ", ".join(f"{i}:{s.label}" for i, s in enumerate(self.slots))
        return f"Rig([{slots_str}])"
