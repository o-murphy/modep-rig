import websocket
import json
import base64
import threading
import requests
import time

# --- Глобальні константи та стан (аналог JS var ...) ---
VERSION = "1.12.0" # Потрібно для запитів /effect/get
cached_cpuLoad = None
cached_xruns = None
timeout_xruns = None
pb_loading = True

class DesktopState:
    def __init__(self, host):
        self.host = host
        self.base_url = f"http://{host}"
        self.pedalboard_plugins = {}
        self.pedalboard_preset_id = 0
        self.pedalboard_preset_name = ""
        self.title = "Untitled"
        self.pedalboard_empty = False
        self.pedalboard_modified = False

    def set_pedalboard_as_modified(self, modified):
        self.pedalboard_modified = modified
        print(f"[State] Modified: {modified}")

class ModAudioClient:
    def __init__(self, host="modduo.local"):
        self.host = host
        self.url = f"ws://{host}/websocket"
        self.desktop = DesktopState(host)
        self.ws = None
        
        self.dataReadyCounter = ''
        self.dataReadyTimeout = None
        self.empty = False
        self.modified = False

    def trigger_delayed_ready_response(self, trigger_new):
        if self.dataReadyTimeout:
            self.dataReadyTimeout.cancel()
            trigger_new = True
        
        if trigger_new:
            def send_msg():
                try:
                    if self.ws and self.ws.sock and self.ws.sock.connected:
                        self.ws.send(f"data_ready {self.dataReadyCounter}")
                except: pass
            self.dataReadyTimeout = threading.Timer(0.05, send_msg)
            self.dataReadyTimeout.start()

    def on_message(self, ws, message):
        global cached_cpuLoad, cached_xruns, timeout_xruns, pb_loading
        
        data = message
        cmd_parts = data.split(" ", 1)
        if not cmd_parts: return
        cmd = cmd_parts[0]

        # 1. Без аргументів
        if cmd == "ping":
            ws.send("pong")
            return
        if cmd == "stop":
            print("UI Blocked")
            return
        if cmd == "cc-device-updated":
            print("CC Device Updated")
            return

        # Відокремлюємо аргументи (data.substr(cmd.length+1))
        args_str = data[len(cmd)+1:] if len(data) > len(cmd) else ""

        # 2. data_ready
        if cmd == "data_ready":
            self.dataReadyCounter = args_str
            self.trigger_delayed_ready_response(True)
            return

        # 3. param_set (в JS до triggerDelayedReadyResponse(false))
        if cmd == "param_set":
            p = args_str.split(" ", 2)
            instance, symbol, value = p[0], p[1], float(p[2])
            print(f"[Param] {instance} {symbol} -> {value}")
            return

        self.trigger_delayed_ready_response(False)

        # 4. Основний масив команд (як в оригіналі)
        
        if cmd == "stats":
            p = args_str.split(" ", 1)
            cpuLoad, xruns = float(p[0]), int(p[1])
            if cpuLoad != cached_cpuLoad:
                cached_cpuLoad = cpuLoad
                print(f"CPU: {cpuLoad}%")
            if xruns != cached_xruns:
                cached_xruns = xruns
                print(f"XRUNS: {xruns}")
            return

        if cmd == "sys_stats":
            p = args_str.split(" ", 2)
            mem, freq, temp = float(p[0]), int(p[1]), int(p[2])
            print(f"SYS: RAM {mem}%, {freq/1000000}GHz, {temp/1000}C")
            return

        if cmd == "output_set":
            p = args_str.split(" ", 2)
            instance, symbol, value = p[0], p[1], float(p[2])
            return

        if cmd == "patch_set":
            sdata = args_str.split(" ", 3)
            instance, writable, uri, vtype = sdata[0], int(sdata[1]) != 0, sdata[2], sdata[3]
            vdata = args_str[len(" ".join(sdata))+1:]
            print(f"Patch Set: {instance} {uri} = {vdata}")
            return

        if cmd == "plugin_pos":
            p = args_str.split(" ", 2)
            instance, x, y = p[0], int(p[1]), int(p[2])
            return

        if cmd == "transport":
            p = args_str.split(" ", 3)
            rolling, bpb, bpm, sync = int(p[0]) != 0, float(p[1]), float(p[2]), p[3]
            return

        if cmd == "preset":
            p = args_str.split(" ", 1)
            instance, value = p[0], (p[1] if p[1] != "null" else "")
            return

        if cmd == "pedal_snapshot":
            p = args_str.split(" ", 1)
            index = int(p[0])
            name = args_str[len(p[0])+1:]
            self.desktop.pedalboard_preset_id = index
            self.desktop.pedalboard_preset_name = name
            return

        if cmd == "hw_map":
            p = args_str.split(" ", 14)
            label = p[6].replace("_", " ")
            dividers = json.loads(p[8].replace("'", '"'))
            page = int(p[9]) if p[9] != "null" else None
            subpage = int(p[10]) if p[10] != "null" else None
            feedback, coloured, momentary = int(p[12])==1, int(p[13])==1, int(p[14])
            print(f"HW Map: {label} on actuator {p[2]}")
            return

        if cmd == "connect":
            p = args_str.split(" ", 1)
            source, target = p[0], p[1]
            print(f"Connect: {source} -> {target}")
            return

        if cmd == "disconnect":
            p = args_str.split(" ", 1)
            source, target = p[0], p[1]
            return

        if cmd == "add":
            p = args_str.split(" ", 6)
            instance, uri, x, y, bypassed, pVer, offBuild = p[0], p[1], float(p[2]), float(p[3]), int(p[4])!=0, p[5], int(p[6])!=0
            
            if instance not in self.desktop.pedalboard_plugins:
                self.desktop.pedalboard_plugins[instance] = {}
                # --- AJAX /effect/get (Критично важливо!) ---
                try:
                    resp = requests.get(f"{self.desktop.base_url}/effect/get", params={
                        'uri': uri, 'version': VERSION, 'plugin_version': pVer
                    })
                    if resp.status_code == 200:
                        print(f"Plugin {uri} added and metadata fetched")
                except Exception as e: print(f"HTTP Error: {e}")
            return

        if cmd == "remove":
            instance = args_str
            if instance == ":all": self.desktop.pedalboard_plugins.clear()
            elif instance in self.desktop.pedalboard_plugins: del self.desktop.pedalboard_plugins[instance]
            return

        if cmd == "loading_start":
            p = args_str.split(" ", 1)
            self.empty, self.modified = int(p[0]) != 0, int(p[1]) != 0
            pb_loading = True
            print("Loading Pedalboard...")
            return

        if cmd == "loading_end":
            snapshotId = int(args_str)
            # --- AJAX /snapshot/name (Критично важливо!) ---
            try:
                resp = requests.get(f"{self.desktop.base_url}/snapshot/name", params={'id': snapshotId})
                if resp.status_code == 200:
                    json_resp = resp.json()
                    self.desktop.pedalboard_preset_name = json_resp.get('name', '')
                    pb_loading = False
                    print(f"Pedalboard Loaded: {self.desktop.pedalboard_preset_name}")
            except Exception as e: print(f"HTTP Error: {e}")
            return

        if cmd == "act_add":
            metadata = json.loads(base64.b64decode(args_str).decode('utf-8'))
            return

        if cmd == "log":
            p = args_str.split(" ", 1)
            ltype, lmsg = int(p[0]), args_str[len(p[0])+1:]
            print(f"[LOG {ltype}] {lmsg}")
            return

        if cmd == "rescan":
            resp = json.loads(base64.b64decode(args_str).decode('utf-8'))
            return

    def run(self):
        # websocket.enableTrace(True) # Розкоментуйте для дебагу сирих даних
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=lambda ws, e: print(f"Error: {e}"),
            on_close=lambda ws, s, m: print("Closed")
        )
        self.ws.run_forever()

if __name__ == "__main__":
    client = ModAudioClient("127.0.0.1:18181") # Вкажіть IP або хост
    client.run()