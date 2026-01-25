import socket
import json
import time


class GuitarixFinalV:
    def __init__(self, host="localhost", port=7000):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((host, port))
        # Ставимо невеликий таймаут, щоб скрипт не вис на читанні
        self.s.settimeout(0.5)

    def notify(self, method, params):
        """Для методів без відповіді (insert, set, order)"""
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        data = json.dumps(payload, separators=(",", ":")) + "\n"
        print(f"-> NOTIFY: {method}")
        self.s.sendall(data.encode())
        # Після notify НЕ читаємо відповідь, щоб не було JSONDecodeError

    def run(self):
        # 1. Вставляємо модулі
        # Згідно з твоїм файлом, позиція 1 та 2
        self.notify("insert_rack_unit", ["gx_distortion", 1, 0])
        self.notify("insert_rack_unit", ["chorus", 2, 0])

        # 2. Встановлюємо порядок
        # ВАЖЛИВО: твоя версія може хотіти список як один об'єкт, спробуємо так:
        self.notify("set_rack_unit_order", ["amp", "gx_distortion", "chorus", "cab"])

        # 3. Вмикаємо (on_off)
        self.notify("set", ["gx_distortion.on_off", 1])
        self.notify("set", ["chorus.on_off", 1])

        # self.notify("plugin_preset_list_load", [1])

        self.notify("set", ["system.engine_state", 0])
        time.sleep(0.2)
        self.notify("set", ["system.engine_state", 1])

        # 4. "Струшуємо" інтерфейс через зміну пресета
        # Це змусить GUI перечитати стан двигуна
        self.notify("set", ["system.current_preset", 3])
        time.sleep(0.2)
        self.notify("set", ["system.current_preset", 4])

        print("[OK] Команди відправлені. Перевір Rack у Guitarix.")


if __name__ == "__main__":
    try:
        gx = GuitarixFinalV()
        gx.run()
    except Exception as e:
        print(f"Error: {e}")
