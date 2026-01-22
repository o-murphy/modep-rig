from collections import defaultdict
from dataclasses import dataclass, field
import time
import threading
from typing import Any, Callable, Type, TypeVar
from urllib.parse import unquote, urlparse

import requests
import websocket

__all__ = ["Client", "WsConnection", "WsProtocol", "WsClient"]


HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}

# WebSocket message types that indicate structural changes (require rig reset)
STRUCTURAL_MESSAGES = frozenset(
    [
        "add",  # Plugin added
        "remove",  # Plugin removed
        "connect",  # Connection made
        "disconnect",  # Connection removed
        "load",  # Pedalboard loaded
        "reset",  # System reset
    ]
)

# Messages to ignore (stats, system info)
IGNORE_MESSAGES = frozenset(["stats", "sys_stats", "ping"])


# -----------------------------
# Event dataclasses
# -----------------------------


@dataclass(frozen=True)
class AddHwPort:
    name: str
    is_output: bool


@dataclass(frozen=True)
class LoadingStart:
    pass


@dataclass(frozen=True)
class LoadingEnd:
    pass


# --------------------
# Pedalboard / Plugin events
@dataclass(frozen=True)
class ParamSet:
    label: str
    symbol: str
    value: float = field(compare=False)


@dataclass(frozen=True)
class ParamSetBypass:
    label: str
    bypassed: bool = field(compare=False)


@dataclass(frozen=True)
class PluginPos:
    label: str
    x: float = field(compare=False)
    y: float = field(compare=False)


@dataclass(frozen=True)
class GenericMessage:
    msg_type: str
    raw_message: str


@dataclass(frozen=True)
class PluginAdd:
    label: str
    uri: str = field(compare=False)
    x: int = field(compare=False)
    y: int = field(compare=False)


@dataclass(frozen=True)
class PluginRemove:
    label: str


# --------------------
# Union of all possible events
WsEvent = (
    AddHwPort
    | LoadingStart
    | LoadingEnd
    | ParamSet
    | ParamSetBypass
    | PluginPos
    | GenericMessage
    | PluginAdd
    | PluginRemove
)


# -----------------------------
# Protocol
# -----------------------------
class WsProtocol:
    IGNORE_MESSAGES = {"stats", "sys_stats", "ping"}

    @staticmethod
    def parse(message: str) -> WsEvent | None:
        parts = message.split()
        if not parts:
            return None
        msg_type = parts[0]
        if msg_type in WsProtocol.IGNORE_MESSAGES:
            return None

        if msg_type == "add_hw_port" and len(parts) >= 5:
            port_path = parts[1]
            port_type = parts[2]
            is_graph_output = parts[3] == "1"
            if port_type == "audio" and port_path.startswith("/graph/"):
                return AddHwPort(name=port_path[7:], is_output=is_graph_output)

        if msg_type == "loading_start":
            return LoadingStart()

        if msg_type == "loading_end":
            return LoadingEnd()

        # plugin_pos /graph/label x y
        if msg_type == "plugin_pos" and len(parts) >= 4:
            graph_path = parts[1]
            if graph_path.startswith("/graph/"):
                label = graph_path[7:]
                try:
                    x = float(parts[2])
                    y = float(parts[3])
                    return PluginPos(label=label, x=x, y=y)
                except ValueError:
                    pass

        if msg_type == "add" and len(parts) >= 3:
            # add instance uri x y bypassed pVersion offBuild
            # parts[0] = "add"
            # parts[1] = instance (e.g., "/graph/DS1_1")
            # parts[2] = uri
            # parts[3] = x, parts[4] = y (optional)
            instance = parts[1]
            uri = parts[2]
            x = None
            y = None
            if len(parts) >= 5:
                try:
                    x = float(parts[3])
                    y = float(parts[4])
                except ValueError:
                    x = None
                    y = None

            if instance.startswith("/graph/"):
                label = instance[7:]
                return PluginAdd(label, uri, x, y)

        if msg_type == "remove" and len(parts) >= 2:
            # remove /graph/label
            graph_path = parts[1]
            if graph_path.startswith("/graph/"):
                label = graph_path[7:]
                return PluginRemove(label)

        if msg_type == "param_set" and len(parts) >= 4:
            graph_path = parts[1]
            symbol = parts[2]
            try:
                value = float(parts[3])
            except ValueError:
                return None
            if graph_path.startswith("/graph/"):
                label = graph_path[7:]
                if symbol == ":bypass":
                    return ParamSetBypass(label=label, bypassed=value > 0.5)
                else:
                    return ParamSet(label=label, symbol=symbol, value=value)

        return GenericMessage(msg_type=msg_type, raw_message=message)


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
            self._ws.send(message)
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


WsEventT = TypeVar("WsEventT", bound=WsEvent)


class StateSnapshot:
    def __init__(self):
        # ключ = тип події, значення = список подій
        self._events: defaultdict[type, list] = defaultdict(list)
        self._lock = threading.RLock()

    def add(self, event):
        """Додати подію"""
        with self._lock:
            self._events[type(event)].append(event)

    def remove(self, event):
        """Видалити конкретну подію"""
        with self._lock:
            events = self._events.get(type(event))
            if events and event in events:
                events.remove(event)
                if not events:
                    del self._events[type(event)]

    def clear(self):
        """Очистити всі події"""
        with self._lock:
            self._events.clear()

    def __getitem__(self, event_type: Type):
        """Отримати список подій певного типу"""
        with self._lock:
            return list(self._events.get(event_type, []))


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

        self._listeners: defaultdict[Type[WsEvent], set[Callable[[WsEvent], None]]] = (
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
        with self._lock:
            self._listeners[event_type].add(cb)

        for event in self._state[event_type]:
            cb(event)

    def off(self, event_type: Type[WsEventT], cb: Callable[[WsEventT], None]):
        with self._lock:
            self._listeners[event_type].discard(cb)

    def _dispatch(self, event: WsEvent):
        # add event to local state
        self._state.add(event)

        with self._lock:
            listeners = list(self._listeners.get(type(event), ()))

        for cb in listeners:
            cb(event)

    # -------------------
    # WsConnection callbacks
    def _on_open(self):
        print(f"Підключено до WebSocket: {self.ws_url}")
        self._state.clear()

    def _on_message(self, message: str):
        event = WsProtocol.parse(message)
        if not event:
            return

        # dispatch
        self._dispatch(event)

        # Log unknown messages
        print(f"WS << {message}")

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
            print(f"WS >> {command}")
            return self.conn.send(command)
        return False

    def effect_bypass(self, label: str, bypass: bool) -> bool:
        return self.effect_param_set(label, ":bypass", 1 if bypass else 0)

    def plugin_pos(self, label: str, x: float, y: float) -> bool:
        command = f"plugin_pos /graph/{label} {float(x)} {float(y)}"
        if self.conn.connected:
            print(f"WS >> {command}")
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

        self.ws = WsClient(self.base_url)
        self.ws.connect()

        self.effects_list = []
        self._load_effects_list()

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
        self.effects_list = data if isinstance(data, list) else []

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
        self.effects_list = data if isinstance(data, list) else []
        return self.effects_list

    def lookup_effect(self, uri: str) -> dict | None:
        """Знайти ефект за URI в кешованому списку"""
        for effect in self.effects_list:
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

    def effect_position(self, label: str, x: int, y: int):
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

    def pedalboard_save(self, title: str = None):
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
