from collections import defaultdict
from dataclasses import dataclass, field
import time
import threading
import weakref
from typing import Any, Callable, Protocol, Type, TypeAlias, TypeVar, cast
from urllib.parse import unquote, urlparse

import requests
import websocket

__all__ = [
    "Client",
    "WsConnection",
    "WsProtocol",
    "WsClient",
    "WsEvent",
    # Events
    "PingEvent",
    "StatsEvent",
    "SysStatsEvent",
    "LoadingStartEvent",
    "LoadingEndEvent",
    "RemoveAllEvent",
    "ResetConnectionsEvent",
    "TransportEvent",
    "TrueBypassEvent",
    "SizeEvent",
    "PbSizeEvent",
    "GraphAddHwPortEvent",
    "GraphConnectEvent",
    "GraphDisconnectEvent",
    "GraphParamSetEvent",
    "GraphParamSetBypassEvent",
    "GraphPluginPosEvent",
    "GraphPluginAddEvent",
    "GraphPluginRemoveEvent",
    "UnknownEvent",
]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}

# Messages to ignore (stats, system info)
IGNORE_MESSAGES = frozenset(["stats", "sys_stats", "ping"])


# -----------------------------
# Event dataclasses
# -----------------------------


@dataclass(frozen=True)
class PingEvent:
    pass


@dataclass(frozen=True)
class StatsEvent:
    _a: float = field(compare=False)
    _b: int = field(compare=False)


@dataclass(frozen=True)
class SysStatsEvent:
    _a: float = field(compare=False)
    _b: int = field(compare=False)
    _c: int = field(compare=False)


@dataclass(frozen=True)
class LoadingStartEvent:
    pass


@dataclass(frozen=True)
class LoadingEndEvent:
    pass


@dataclass(frozen=True)
class RemoveAllEvent:
    pass


@dataclass(frozen=True)
class ResetConnectionsEvent:
    pass


@dataclass(frozen=True)
class TransportEvent:
    _any: Any


@dataclass(frozen=True)
class TrueBypassEvent:
    _a: int
    _b: int


@dataclass(frozen=True)
class SizeEvent:
    _a: int
    _b: int


@dataclass(frozen=True)
class PbSizeEvent:
    x: int
    y: int


@dataclass(frozen=True)
class GraphAddHwPortEvent:
    name: str
    is_output: bool


@dataclass(frozen=True)
class GraphConnectEvent:
    """connect /graph/gx_duck_delay__ND258bdR/out /graph/gx_fuzz__4e4UwTyJ/in"""

    src_path: str
    dst_path: str


@dataclass(frozen=True)
class GraphDisconnectEvent:
    """disconnect /graph/gx_duck_delay__ND258bdR/out /graph/gx_fuzz__4e4UwTyJ/in"""

    src_path: str
    dst_path: str


@dataclass(frozen=True)
class GraphParamSetEvent:
    label: str
    symbol: str
    value: float = field(compare=False)


@dataclass(frozen=True)
class GraphParamSetBypassEvent:
    label: str
    bypassed: bool = field(compare=False)


@dataclass(frozen=True)
class GraphPluginPosEvent:
    label: str
    x: float = field(compare=False)
    y: float = field(compare=False)


@dataclass(frozen=True)
class UnknownEvent:
    msg_type: str
    raw_message: str


@dataclass(frozen=True)
class GraphPluginAddEvent:
    label: str
    uri: str = field(compare=False)
    x: float = field(compare=False, default=0)
    y: float = field(compare=False, default=0)


@dataclass(frozen=True)
class GraphPluginRemoveEvent:
    label: str


# --------------------
# Union of all possible events
WsEvent = (
    PingEvent
    | StatsEvent
    | SysStatsEvent
    | LoadingStartEvent
    | LoadingEndEvent
    | RemoveAllEvent
    | ResetConnectionsEvent
    | TransportEvent
    | TrueBypassEvent
    | SizeEvent
    | PbSizeEvent
    | GraphAddHwPortEvent
    | GraphConnectEvent
    | GraphDisconnectEvent
    | GraphParamSetEvent
    | GraphParamSetBypassEvent
    | GraphPluginPosEvent
    | GraphPluginAddEvent
    | GraphPluginRemoveEvent
    | UnknownEvent
)

WsEventT = TypeVar("WsEventT", bound=WsEvent, covariant=True)


class EventCallBack(Protocol[WsEventT]):
    def __call__(self, WsEventT) -> None: ...


EventCallBackRef: TypeAlias = (
    weakref.ReferenceType[EventCallBack] | weakref.WeakMethod[EventCallBack]
)


# -----------------------------
# Protocol
# -----------------------------
class WsProtocol:
    GRAPH_PREFIX = "/graph/"

    @staticmethod
    def parse(message: str) -> WsEvent | None:
        parts: list[str] = message.split()
        prefix = WsProtocol.GRAPH_PREFIX

        match parts:
            case ["ping", *_]:
                return PingEvent()

            case ["stats", _a, _b, *_]:
                try:
                    return StatsEvent(float(_a), int(_b))
                except ValueError:
                    pass

            case ["sys_stats", _a, _b, _c, *_]:
                try:
                    return SysStatsEvent(float(_a), int(_b), int(_c))
                except ValueError:
                    pass

            case ["loading_start", *_]:
                # received 2 values like (1, 1) but we ignoring it
                return LoadingStartEvent()

            case ["loading_end", *_]:
                # received 2 values like (0, 0) but we ignoring it
                return LoadingEndEvent()

            # audio port
            case ["add_hw_port", path, "audio", is_out, *_]:
                return GraphAddHwPortEvent(
                    name=path.removeprefix(prefix),
                    is_output=is_out == "1",
                )

            # plugin_pos /graph/label x y
            case ["plugin_pos", inst, rx, ry, *_]:
                try:
                    x: float = float(rx)
                    y: float = float(ry)
                    return GraphPluginPosEvent(
                        label=inst.removeprefix(prefix), x=x, y=y
                    )
                except ValueError:
                    None

            case ["add", inst, uri, rx, ry, *_]:
                try:
                    x, y = float(rx), float(ry)
                except ValueError:
                    x, y = 0, 0
                return GraphPluginAddEvent(inst.removeprefix(prefix), uri, x, y)

            case ["add", inst, uri, *_]:
                return GraphPluginAddEvent(inst.removeprefix(prefix), uri, 0, 0)

            case ["remove", ":all"]:
                return RemoveAllEvent()

            case ["remove", inst, *_]:
                # remove /graph/label
                return GraphPluginRemoveEvent(inst.removeprefix(prefix))

            case ["connect" | "disconnect" as action, src, dst, *_]:
                event_cls = (
                    GraphConnectEvent if action == "connect" else GraphDisconnectEvent
                )
                return event_cls(
                    src.removeprefix(prefix),
                    dst.removeprefix(prefix),
                )

            case ["resetConnections", *_]:
                return ResetConnectionsEvent()

            case ["transport", *_any]:
                TransportEvent(_any)

            case ["true_bypass", _a, _b, *_]:
                try:
                    return TrueBypassEvent(int(_a), int(_b))
                except ValueError:
                    return None

            case ["size", _a, _b, *_]:
                try:
                    SizeEvent(int(_a), int(_b))
                except ValueError:
                    return None

            case ["pb_size", rx, ry, *_]:
                try:
                    return PbSizeEvent(int(rx), int(ry))
                except ValueError:
                    return None

            case ["param_set", inst, symbol, val, *_]:
                try:
                    f_val = float(val)
                except ValueError:
                    return None
                label = inst.removeprefix(prefix)
                if symbol == ":bypass":
                    return GraphParamSetBypassEvent(label=label, bypassed=f_val > 0.5)
                return GraphParamSetEvent(label=label, symbol=symbol, value=f_val)
            case [msg_type, *_]:
                return UnknownEvent(msg_type=msg_type, raw_message=message)
        return None


class WsConnection:
    def __init__(
        self,
        ws_url: str,
        on_open: Callable[[], None] | None = None,
        on_message: Callable[[str], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_close: Callable[[], None] | None = None,
        reconnect_delay: float = 2.0,
        auto_reconnect: bool = True,
    ):
        self.ws_url = ws_url
        self._on_open = on_open
        self._on_message = on_message
        self._on_error = on_error
        self._on_close = on_close

        self._reconnect_delay = reconnect_delay
        self._auto_reconnect = auto_reconnect

        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._should_run = False
        self._connected = threading.Event()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def connect(self):
        """Start WebSocket connection in background thread."""
        if self._thread and self._thread.is_alive():
            return

        self._should_run = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def disconnect(self):
        """Stop connection and disable auto-reconnect."""
        self._should_run = False
        self._connected.clear()
        if self._ws:
            self._ws.close()

    def send(self, message: str) -> bool:
        """Send raw message over WebSocket."""
        if not self.connected:
            return False
        try:
            if self._ws is not None and self.connected:
                self._ws.send(message)
            print(f"WS >> {message}")
            return True
        except Exception as e:
            if self._on_error:
                self._on_error(e)
            return False

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _run_loop(self):
        while self._should_run:
            self._ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self._handle_open,
                on_message=self._handle_message,
                on_error=self._handle_error,
                on_close=self._handle_close,
            )

            # Blocking call
            self._ws.run_forever()

            self._connected.clear()

            if not self._should_run or not self._auto_reconnect:
                break

            time.sleep(self._reconnect_delay)

    # ------------------------------------------------------------------ #
    # WebSocket callbacks
    # ------------------------------------------------------------------ #

    def _handle_open(self, ws):
        self._connected.set()
        if self._on_open:
            self._on_open()

    def _handle_message(self, ws, message: str):
        if self._on_message:
            self._on_message(message)

    def _handle_error(self, ws, error):
        if self._on_error:
            self._on_error(error)

    def _handle_close(self, ws, code, reason):
        self._connected.clear()
        if self._on_close:
            self._on_close()


class StateSnapshot:
    def __init__(self):
        # Використовуємо dict для O(1) пошуку еквівалентних подій
        self._events: defaultdict[type, dict] = defaultdict(dict)
        self._lock = threading.RLock()

    def add(self, event):
        """
        Додає подію. Якщо еквівалентна подія (за правилами dataclass)
        вже існує — вона буде оновлена новим значенням.
        """
        with self._lock:
            event_type = type(event)
            # 1. Якщо подія вже є (наприклад, той самий параметр іншого значення),
            # pop видалить стару версію, щоб нова стала в кінець черги.
            self._events[event_type].pop(event, None)

            # 2. Додаємо нову версію події
            self._events[event_type][event] = None

    def remove(self, event):
        """Видалити конкретну подію"""
        with self._lock:
            events_dict = self._events.get(type(event))
            if events_dict:
                events_dict.pop(event, None)
                if not events_dict:
                    del self._events[type(event)]

    def clear(self):
        """Очистити всі події"""
        with self._lock:
            self._events.clear()

    def __getitem__(self, event_type: Type):
        """Отримати список подій певного типу"""
        with self._lock:
            # Повертає впорядкований список унікальних за структурою подій
            return list(self._events.get(event_type, {}).keys())


# -----------------------------
# WsClient
# -----------------------------
class WsClient:
    def __init__(self, base_url: str):
        parsed = urlparse(base_url)
        is_secure = parsed.scheme == "https"
        scheme = "wss" if is_secure else "ws"
        hostname = parsed.hostname or parsed.path.split(":")[0]
        port = parsed.port or (443 if is_secure else 18181)
        self.ws_url = f"{scheme}://{hostname}:{port}/websocket"
        print("WS:", self.ws_url)

        self._state = StateSnapshot()

        self._listeners: defaultdict[Type[WsEvent], set[EventCallBackRef]] = (
            defaultdict(set)
        )
        self._lock = threading.RLock()

        # Transport
        self.conn = WsConnection(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def on(self, event_type: Type[WsEventT], cb: Callable[[WsEventT], None]):
        ref: EventCallBackRef
        cb_any = cast(EventCallBack, cb)
        key = cast(type[WsEvent], event_type)

        try:
            ref = weakref.WeakMethod(cb_any)  # type: ignore[arg-type] # bound method
        except TypeError:
            ref = weakref.ref(cb_any)

        with self._lock:
            self._listeners[key].add(ref)

        # replay state (type-safe)
        for event in self._state[event_type]:
            cb(event)

    def off(self, event_type: Type[WsEventT], cb: Callable[[WsEventT], None]):
        ref: EventCallBackRef
        cb_any = cast(EventCallBack, cb)
        key = cast(type[WsEvent], event_type)

        with self._lock:
            refs = self._listeners.get(key)
            if not refs:
                return
            for ref in list(refs):
                if ref() is cb_any:
                    refs.remove(ref)

    def _dispatch(self, event: WsEvent):
        # add event to local state
        self._state.add(event)

        with self._lock:
            refs = list(self._listeners.get(type(event), ()))

        dead: list[EventCallBackRef] = []

        for ref in refs:
            cb = ref()
            if cb is None:
                dead.append(ref)
            else:
                cb(event)

        if dead:
            with self._lock:
                self._listeners[type(event)].difference_update(dead)

    # -------------------
    # WsConnection callbacks
    def _on_open(self):
        print(f"Підключено до WebSocket: {self.ws_url}")
        self._state.clear()

    def _on_message(self, message: str):
        # Log unknown messages
        print(f"WS << {message}")

        event = WsProtocol.parse(message)
        if not event:
            return

        if isinstance(event, PingEvent):
            self.conn.send("pong")

        # dispatch
        self._dispatch(event)

    def _on_error(self, error):
        print(f"WS Помилка: {error}")

    def _on_close(self):
        print("🔌 WebSocket з'єднання закрито")
        self._state.clear()

    # -------------------
    # Public API
    def connect(self):
        self.conn.connect()

    def disconnect(self):
        self.conn.disconnect()

    def effect_param_set(self, label: str, symbol: str, value) -> bool:
        command = f"param_set /graph/{label}/{symbol} {value}"
        if self.conn.connected:
            return self.conn.send(command)
        return False

    def effect_bypass(self, label: str, bypass: bool) -> bool:
        return self.effect_param_set(label, ":bypass", 1 if bypass else 0)

    def plugin_pos(self, label: str, x: float, y: float) -> bool:
        command = f"plugin_pos /graph/{label} {float(x)} {float(y)}"
        if self.conn.connected:
            return self.conn.send(command)
        return False


# -----------------------------
# Client
# -----------------------------
class Client:
    def __init__(self, base_url: str):
        """
        Client for MOD server.

        Args:
            base_url: Server base URL
            connect: If True (default) the WebSocket client is started immediately.
                     If False, the caller should call `client.ws.connect()` after
                     installing callbacks to avoid missing early WS messages.
        """
        self.base_url = base_url
        self.version = self._get_version()

        self.plugins_list: list[dict] = []
        self._load_effects_list()

        self.ws = WsClient(self.base_url)
        # self.ws.connect()

    def _get_version(self) -> str:
        try:
            resp = requests.get(self.base_url, headers=HEADERS, allow_redirects=False)
            if resp.status_code in [301, 302]:
                location = resp.headers.get("Location", "")
                version = unquote(location).split("v=")[-1]
                print(f"Detected MOD Version: {version}")
                return version
        except Exception as e:
            print(f"Warning: Could not resolve version: {e}")
        return "0.0.0"

    def _load_effects_list(self):
        data = self._get("/effect/list")
        self.plugins_list = data if isinstance(data, list) else []

    def _get(self, path: str, **kwargs):
        url = self.base_url + path
        print(f"GET {url}")
        if kwargs:
            print(f"    params: {kwargs}")

        resp = requests.get(url, params=kwargs, headers=HEADERS)
        return self._parse_response(resp)

    def _post(self, path: str, payload: str):
        """POST request with text/plain payload."""
        url = self.base_url + path
        print(f"POST {url}")
        print(f"    payload: {payload}")

        resp = requests.post(
            url, data=payload, headers={**HEADERS, "Content-Type": "text/plain"}
        )
        return self._parse_response(resp)

    def _parse_response(self, resp: requests.Response):
        """Parse response from GET or POST request."""
        if resp.status_code >= 400:
            print(f"    ERROR: HTTP {resp.status_code}")
            return None

        text = resp.text.strip()

        if text.lower() == "true":
            print("    OK: True")
            return True
        if text.lower() == "false":
            print("    OK: False")
            return False

        try:
            data = resp.json()
            print(f"    OK: {type(data).__name__}")
            return data
        except requests.exceptions.JSONDecodeError:
            print(f"    OK: {text[:50]}..." if len(text) > 50 else f"    OK: {text}")
            return text if text else None

    # =========================================================================
    # Effects API
    # =========================================================================

    def effect_list(self):
        """Отримати список всіх доступних ефектів"""
        data = self._get("/effect/list")
        self.plugins_list = data if isinstance(data, list) else []
        return self.plugins_list

    def lookup_effect(self, uri: str) -> dict | None:
        """Знайти ефект за URI в кешованому списку"""
        for effect in self.plugins_list:
            if effect.get("uri") == uri:
                return effect
        return None

    def effect_get(self, uri: str):
        """Отримати детальну інформацію про ефект"""
        return self._get("/effect/get", uri=uri, version=self.version)

    def effect_add(
        self, label: str, uri: str, x: int = 200, y: int = 400
    ) -> dict | None:
        """Додати ефект на граф"""
        return self._get(f"/effect/add//graph/{label}", uri=uri, x=x, y=y)

    def effect_remove(self, label: str) -> bool:
        """Видалити ефект з графа"""
        result = self._get(f"/effect/remove//graph/{label}")
        return result is True

    def effect_connect(self, output: str, input: str) -> bool:
        """З'єднати два порти"""
        result = self._get(f"/effect/connect//graph/{output},/graph/{input}")
        return result is True

    def effect_disconnect(self, output: str, input: str) -> bool:
        """Роз'єднати два порти"""
        result = self._get(f"/effect/disconnect//graph/{output},/graph/{input}")
        return result is True

    def effect_bypass(self, label, bypass: bool) -> Any:
        return self.effect_param_set(label, ":bypass", 1 if bypass else 0)

    def effect_param_set(self, label: str, symbol: str, value: Any):
        return self._post("/effect/parameter/set/", f"/graph/{label}/{symbol}/{value}")

    def effect_preset_load(self, label: str, preset_uri: str):
        """Завантажити пресет для ефекту"""
        return self._get(f"/effect/preset/load//graph/{label}", uri=preset_uri)

    def effect_position(self, label: str, x: float, y: float):
        """Змінити позицію ефекту на UI"""
        # Prefer WebSocket plugin_pos command when available (real-time UI placement)
        try:
            if self.ws and self.ws.plugin_pos(label, x, y):
                return True
        except Exception as e:
            print(f"  WebSocket position failed, using REST fallback: {e}")

        # Fallback to REST endpoint
        return self._get(f"/effect/position//graph/{label}/{x}/{y}")

    # =========================================================================
    # Pedalboard API
    # =========================================================================

    def pedalboard_list(self):
        """Отримати список всіх педалбордів"""
        return self._get("/pedalboard/list")

    def pedalboard_current(self):
        """Отримати поточний стан педалборда"""
        return self._get("/pedalboard/current")

    def pedalboard_load_bundle(self, pedalboard: str, is_default: int = 0):
        """Завантажити педалборд з бандла"""
        return self._get(
            "/pedalboard/load_bundle", bundlepath=pedalboard, isDefault=is_default
        )

    def pedalboard_save(self, title: str | None = None):
        """Зберегти поточний педалборд"""
        params = {}
        if title:
            params["title"] = title
        return self._get("/pedalboard/save", **params)

    def pedalboard_save_as(self, title: str):
        """Зберегти педалборд під новим ім'ям"""
        return self._get("/pedalboard/save_as", title=title)

    def pedalboard_remove(self, bundlepath: str):
        """Видалити педалборд"""
        return self._get("/pedalboard/remove", bundlepath=bundlepath)

    def pedalboard_info(self, bundlepath: str):
        """Отримати інформацію про педалборд"""
        return self._get("/pedalboard/info", bundlepath=bundlepath)

    # =========================================================================
    # Snapshot API
    # =========================================================================

    def snapshot_list(self):
        """Отримати список снепшотів"""
        return self._get("/snapshot/list")

    def snapshot_load(self, snapshot_id: int):
        """Завантажити снепшот"""
        return self._get(f"/snapshot/load/{snapshot_id}")

    def snapshot_save(self):
        """Зберегти поточний снепшот"""
        return self._get("/snapshot/save")

    def snapshot_save_as(self, name: str):
        """Зберегти снепшот під новим ім'ям"""
        return self._get("/snapshot/save_as", name=name)

    def snapshot_remove(self, snapshot_id: int):
        """Видалити снепшот"""
        return self._get(f"/snapshot/remove/{snapshot_id}")

    # =========================================================================
    # Banks API
    # =========================================================================

    def banks_list(self):
        """Отримати список банків"""
        return self._get("/banks/list")

    def banks_save(self):
        """Зберегти банки"""
        return self._get("/banks/save")

    # =========================================================================
    # MIDI API
    # =========================================================================

    def midi_learn(self, label: str, symbol: str):
        """Почати MIDI learn для параметра"""
        return self._get(f"/effect/midi/learn//graph/{label}/{symbol}")

    def midi_map(
        self,
        label: str,
        symbol: str,
        channel: int,
        cc: int,
        minimum: float = 0.0,
        maximum: float = 1.0,
    ):
        """Призначити MIDI CC на параметр"""
        return self._get(
            f"/effect/midi/map//graph/{label}/{symbol}/{channel}/{cc}/{minimum}/{maximum}"
        )

    def midi_unmap(self, label: str, symbol: str):
        """Видалити MIDI mapping з параметра"""
        return self._get(f"/effect/midi/unmap//graph/{label}/{symbol}")

    # =========================================================================
    # System API
    # =========================================================================

    def ping(self):
        """Health check"""
        return self._get("/ping")

    def reset(self):
        """Скинути стан (видалити всі ефекти)"""
        return self._get("/reset")

    def system_info(self):
        """Отримати інформацію про систему"""
        return self._get("/system/info")

    def system_prefs(self):
        """Отримати системні налаштування"""
        return self._get("/system/prefs")
