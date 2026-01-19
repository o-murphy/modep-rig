# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "tomlkit",
# ]
# ///

import json
import os
from tomlkit import parse, table, aot


def merge_json_to_toml(json_path, toml_path):
    try:
        if not os.path.exists(toml_path):
            print(f"Помилка: {toml_path} не знайдено!")
            return

        # 1. Читаємо існуючий конфіг
        with open(toml_path, "r", encoding="utf-8") as f:
            content = f.read()
            config = parse(content)

        # 2. Читаємо JSON
        with open(json_path, "r", encoding="utf-8") as f:
            json_plugins = json.load(f)

        # Перевіряємо наявність або створюємо секцію [[plugins]]
        if "plugins" not in config:
            # Створюємо масив таблиць (Array of Tables)
            config.add("plugins", aot())

        # Отримуємо існуючі URI для перевірки дублікатів
        # tomlkit об'єкти поводяться як словники/списки
        existing_uris = set()
        for p in config.get("plugins", []):
            if "uri" in p:
                existing_uris.add(p["uri"])

        new_plugins_count = 0

        # 3. Додаємо нові плагіни
        plugins_aot = config["plugins"]

        for jp in json_plugins:
            uri = jp.get("uri")
            if uri and uri not in existing_uris:
                cat_list = jp.get("category", [])

                # Створюємо нову таблицю для плагіна
                new_entry = table()
                new_entry.add("name", jp.get("name", "Unknown"))
                new_entry.add("uri", uri)
                new_entry.add(
                    "category", cat_list[0].lower() if cat_list else "utility"
                )

                # Додаємо в масив
                plugins_aot.append(new_entry)
                existing_uris.add(uri)
                new_plugins_count += 1

        # 4. Зберігаємо (tomlkit зберігає коментарі та форматування шапки)
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write(config.as_string())

        print(f"✅ Успішно! Додано нових плагінів: {new_plugins_count}")

    except Exception as e:
        print(f"❌ Помилка: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    merge_json_to_toml("plugins.json", "config.toml")
