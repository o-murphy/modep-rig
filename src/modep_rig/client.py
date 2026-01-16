import time
import threading
from typing import Callable
from urllib.parse import unquote, urlparse

import requests
import websocket

__all__ = ["Client"]


HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
}

# WebSocket message types that indicate structural changes (require rig reset)
STRUCTURAL_MESSAGES = frozenset([
    "add",           # Plugin added
    "remove",        # Plugin removed
    "connect",       # Connection made
    "disconnect",    # Connection removed
    "load",          # Pedalboard loaded
    "reset",         # System reset
])

# Messages to ignore (stats, system info)
IGNORE_MESSAGES = frozenset([
    "stats",
    "sys_stats",
])


class WsClient:
    def __init__(self, base_url: str):
        parsed = urlparse(base_url)
        hostname = parsed.hostname if parsed.hostname else parsed.path.split(':')[0]
        self.ws_url = f"ws://{hostname}:18181/websocket"
        self.ws = None
        self._should_reconnect = True

        # Callbacks
        self._on_param_change: Callable[[str, str, float], None] | None = None
        self._on_bypass_change: Callable[[str, bool], None] | None = None
        self._on_structural_change: Callable[[str, str], None] | None = None

    def set_callbacks(
        self,
        on_param_change: Callable[[str, str, float], None] | None = None,
        on_bypass_change: Callable[[str, bool], None] | None = None,
        on_structural_change: Callable[[str, str], None] | None = None,
    ):
        """Set callbacks for WebSocket events.

        Args:
            on_param_change: Called when parameter changes (label, symbol, value)
            on_bypass_change: Called when bypass changes (label, bypassed)
            on_structural_change: Called when structure changes - plugins/connections (msg_type, raw_message)
        """
        self._on_param_change = on_param_change
        self._on_bypass_change = on_bypass_change
        self._on_structural_change = on_structural_change

    def on_open(self, ws):
        print(f"Підключено до WebSocket: {self.ws_url}")

    def on_message(self, ws, message: str):
        """Parse and dispatch WebSocket messages."""
        parts = message.split()
        if not parts:
            return

        msg_type = parts[0]

        # Ignore stats messages
        if msg_type in IGNORE_MESSAGES:
            return

        print(f"WS << {message}")

        # Structural changes - plugins, connections, pedalboard
        if msg_type in STRUCTURAL_MESSAGES:
            if self._on_structural_change:
                self._on_structural_change(msg_type, message)
            return

        # Parameter change: param_set /graph/label symbol value
        if msg_type == "param_set" and len(parts) >= 4:
            # Format: param_set /graph/autowah_1 freq 0.261719
            graph_path = parts[1]  # /graph/autowah_1
            symbol = parts[2]       # freq
            try:
                value = float(parts[3])
            except ValueError:
                return

            # Parse label from path: /graph/label -> label
            if graph_path.startswith("/graph/"):
                label = graph_path[7:]  # Remove "/graph/"

                # Check if it's bypass
                if symbol == ":bypass":
                    if self._on_bypass_change:
                        self._on_bypass_change(label, value > 0.5)
                else:
                    if self._on_param_change:
                        self._on_param_change(label, symbol, value)
            return

        # Output parameter (for future use)
        if msg_type == "output" and len(parts) >= 4:
            # output /graph/label symbol value
            # TODO: implement output parameter handling
            pass

    def on_error(self, ws, error):
        print(f"WS Помилка: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        print("🔌 WebSocket з'єднання закрито")
        if self._should_reconnect:
            print("🔄 Спроба відновлення через 2 секунди...")
            time.sleep(2)

    def connect(self):
        """Запуск клієнта у фоновому потоці з авто-реконнектом."""
        def run_loop():
            while self._should_reconnect:
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                # run_forever блокує потік, поки з'єднання живе
                self.ws.run_forever()
                
                if not self._should_reconnect:
                    break
                time.sleep(1) # Невелика пауза перед наступною спробою підключення

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()

    def disconnect(self):
        """Метод для коректного закриття без реконнекту."""
        self._should_reconnect = False
        if self.ws:
            self.ws.close()

    def effect_parameter_set(self, label: str, symbol: str, value):
        # Перевірка наявності сокета та активності з'єднання
        if self.ws and self.ws.sock and self.ws.sock.connected:
            command = f"param_set /graph/{label}/{symbol} {value}"
            try:
                print("DEBUG:", command)
                self.ws.send(command)
                return True
            except Exception as e:
                print(f"⚠️ Помилка відправки: {e}")
        return False

    def effect_bypass(self, label: str, bypass: bool):
        value = 1 if bypass else 0
        return self.effect_parameter_set(label, ":bypass", value)

class Client:
    def __init__(self, base_url: str):
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
        data = self._request("/effect/list")
        self.effects_list = data if isinstance(data, list) else []
        import json
        with open("plugins.json", "w") as fp:
            json.dump(data, fp)

    def _request(self, path: str, **kwargs):
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
            print(f"    OK: True")
            return True
        if text.lower() == "false":
            print(f"    OK: False")
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
        data = self._request("/effect/list")
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
        return self._request("/effect/get", uri=uri, version=self.version)

    def effect_add(
        self, label: str, uri: str, x: int = 200, y: int = 400
    ) -> dict | None:
        """Додати ефект на граф"""
        return self._request(f"/effect/add//graph/{label}", uri=uri, x=x, y=y)

    def effect_remove(self, label: str) -> bool:
        """Видалити ефект з графа"""
        result = self._request(f"/effect/remove//graph/{label}")
        return result is True

    def effect_connect(self, output: str, input: str) -> bool:
        """З'єднати два порти"""
        result = self._request(f"/effect/connect//graph/{output},/graph/{input}")
        return result is True

    def effect_disconnect(self, output: str, input: str) -> bool:
        """Роз'єднати два порти"""
        result = self._request(f"/effect/disconnect//graph/{output},/graph/{input}")
        return result is True

    # NOTE: use self.ws.effect_bypass
    # def effect_bypass(self, label: str, bypass: bool) -> bool:
    #     """Увімкнути/вимкнути bypass для ефекту (через :bypass параметр)"""
    #     value = 1 if bypass else 0
    #     return self.effect_parameter_set(label, ":bypass", value)

    # NOTE: use self.ws.effect_parameter_set
    # def effect_parameter_set(self, label: str, symbol: str, value: float) -> bool:
    #     """Встановити значення параметра ефекту"""
    #     payload = f"/graph/{label}/{symbol}/{value}"
    #     result = self._post("/effect/parameter/set/", payload)
    #     return result is True

    def effect_parameter_get(self, label: str, symbol: str):
        """Отримати значення параметра ефекту"""
        return self._request(f"/effect/parameter/get//graph/{label}/{symbol}")

    def effect_preset_load(self, label: str, preset_uri: str):
        """Завантажити пресет для ефекту"""
        return self._request(f"/effect/preset/load//graph/{label}", uri=preset_uri)

    def effect_position(self, label: str, x: int, y: int):
        """Змінити позицію ефекту на UI"""
        return self._request(f"/effect/position//graph/{label}/{x}/{y}")

    # =========================================================================
    # Pedalboard API
    # =========================================================================

    def pedalboard_list(self):
        """Отримати список всіх педалбордів"""
        return self._request("/pedalboard/list")

    def pedalboard_current(self):
        """Отримати поточний стан педалборда"""
        return self._request("/pedalboard/current")

    def pedalboard_load_bundle(self, pedalboard: str, is_default: int = 0):
        """Завантажити педалборд з бандла"""
        return self._request(
            "/pedalboard/load_bundle", bundlepath=pedalboard, isDefault=is_default
        )

    def pedalboard_save(self, title: str = None):
        """Зберегти поточний педалборд"""
        params = {}
        if title:
            params["title"] = title
        return self._request("/pedalboard/save", **params)

    def pedalboard_save_as(self, title: str):
        """Зберегти педалборд під новим ім'ям"""
        return self._request("/pedalboard/save_as", title=title)

    def pedalboard_remove(self, bundlepath: str):
        """Видалити педалборд"""
        return self._request("/pedalboard/remove", bundlepath=bundlepath)

    def pedalboard_info(self, bundlepath: str):
        """Отримати інформацію про педалборд"""
        return self._request("/pedalboard/info", bundlepath=bundlepath)

    # =========================================================================
    # Snapshot API
    # =========================================================================

    def snapshot_list(self):
        """Отримати список снепшотів"""
        return self._request("/snapshot/list")

    def snapshot_load(self, snapshot_id: int):
        """Завантажити снепшот"""
        return self._request(f"/snapshot/load/{snapshot_id}")

    def snapshot_save(self):
        """Зберегти поточний снепшот"""
        return self._request("/snapshot/save")

    def snapshot_save_as(self, name: str):
        """Зберегти снепшот під новим ім'ям"""
        return self._request("/snapshot/save_as", name=name)

    def snapshot_remove(self, snapshot_id: int):
        """Видалити снепшот"""
        return self._request(f"/snapshot/remove/{snapshot_id}")

    # =========================================================================
    # Banks API
    # =========================================================================

    def banks_list(self):
        """Отримати список банків"""
        return self._request("/banks/list")

    def banks_save(self):
        """Зберегти банки"""
        return self._request("/banks/save")

    # =========================================================================
    # MIDI API
    # =========================================================================

    def midi_learn(self, label: str, symbol: str):
        """Почати MIDI learn для параметра"""
        return self._request(f"/effect/midi/learn//graph/{label}/{symbol}")

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
        return self._request(
            f"/effect/midi/map//graph/{label}/{symbol}/{channel}/{cc}/{minimum}/{maximum}"
        )

    def midi_unmap(self, label: str, symbol: str):
        """Видалити MIDI mapping з параметра"""
        return self._request(f"/effect/midi/unmap//graph/{label}/{symbol}")

    # =========================================================================
    # System API
    # =========================================================================

    def ping(self):
        """Health check"""
        return self._request("/ping")

    def reset(self):
        """Скинути стан (видалити всі ефекти)"""
        return self._request("/reset")

    def system_info(self):
        """Отримати інформацію про систему"""
        return self._request("/system/info")

    def system_prefs(self):
        """Отримати системні налаштування"""
        return self._request("/system/prefs")
