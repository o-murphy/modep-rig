import sys
import subprocess
import os
from pathlib import Path

def main():
    # Шлях до поточної директорії, де лежать gui.py та service.py
    base_path = Path(__file__).parent
    
    # Визначаємо, чи хоче користувач запустити сервіс без графіки
    # Ми перевіряємо аргументи вручну, щоб не "ковтати" їх для підпроцесів
    is_headless = "--headless" in sys.argv
    
    # Видаляємо наш службовий прапор --headless, щоб він не заважав іншим скриптам
    filtered_args = [a for a in sys.argv[1:] if a != "--headless"]

    # Логіка вибору скрипта
    # Якщо примусово headless АБО якщо немає змінної DISPLAY (для Linux без X11)
    if is_headless or (sys.platform == "linux" and not os.environ.get("DISPLAY")):
        target_script = base_path / "service.py"
        mode_name = "SERVICE (Headless)"
    else:
        target_script = base_path / "gui.py"
        mode_name = "GUI"

    if not target_script.exists():
        print(f"Error: {target_script.name} not found in {base_path}")
        sys.exit(1)

    print(f"--- Starting MODEP Rack in {mode_name} mode ---")

    # Формуємо команду для запуску
    # Використовуємо sys.executable, щоб гарантувати використання того ж віртуального середовища
    cmd = [sys.executable, str(target_script)] + filtered_args

    try:
        # Запускаємо процес. 
        # Використовуємо subprocess.run для service.py, щоб чекати завершення (Ctrl+C)
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        # Обробка Ctrl+C на рівні головного процесу
        pass
    except subprocess.CalledProcessError as e:
        print(f"\nProcess finished with error code: {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()