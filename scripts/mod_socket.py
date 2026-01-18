import websocket
import random
import time
import threading

# Адреса MODEP
WS_URL = "ws://127.0.0.1:18181/websocket"

# Глобальна змінна для сокета
ws_client = None

def send_random_parameters():
    global ws_client
    print(">>> Цикл рандомізації запущено.")
    
    while True:
        try:
            if ws_client and ws_client.sock and ws_client.sock.connected:
                val = random.uniform(0.0, 1.0)
                
                # ФОРМАТ: param_set [шлях/до/параметра] [значення]
                # Зверніть увагу на слеш між cs_chorus1_1 та mod_freq_2
                command = f"param_set /graph/cs_chorus1_1/mod_freq_2 {val:.15f}"
                
                ws_client.send(command)
                print(f"Відправлено: {command}")
            else:
                # Чекаємо на з'єднання
                pass
        except Exception as e:
            print(f"Помилка: {e}")
        
        time.sleep(1)

def on_message(ws, message):
    # Фільтруємо технічну інформацію
    if "stats" not in message and "ping" not in message:
        print(f"MODEP: {message}")

def on_open(ws):
    print("--- З'єднання встановлено! ---")

def run_ws():
    global ws_client
    while True:
        ws_client = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
        )
        # ping_interval утримує з'єднання
        ws_client.run_forever(ping_interval=10, ping_timeout=5)
        print("З'єднання втрачено. Перепідключення...")
        time.sleep(2)

if __name__ == "__main__":
    # 1. Запускаємо потік логіки (тепер без аргументів, через глобальну змінну)
    logic_thread = threading.Thread(target=send_random_parameters, daemon=True)
    logic_thread.start()
    
    # 2. Запускаємо основний цикл WebSocket
    try:
        run_ws()
    except KeyboardInterrupt:
        print("Зупинка програми...")