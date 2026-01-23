from mod_rack.config import Config
from mod_rack.rack import Rack


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import time

    # Завантажуємо конфігурацію
    config = Config.load("config.toml")

    print(f"Loaded {len(config.plugins)} plugins from config")
    print(f"Categories: {config.list_categories()}")
    print(f"Hardware inputs: {config.hardware.inputs}")
    print(f"Hardware outputs: {config.hardware.outputs}")

    # Створюємо Rack з конфігурації
    rack = Rack(config)
    print(rack)

    time.sleep(1)

    # Тепер можна використовувати імена замість URI!
    print("\n" + "=" * 60)
    print("Adding DS1 to slot 0 (by name)")
    print("=" * 60)

    rack[0] = "DS1"
    print(rack)

    time.sleep(1)

    print("\n" + "=" * 60)
    print("Adding Paranoia to slot 1 (by name)")
    print("=" * 60)

    rack[1] = "Paranoia"
    print(rack)

    time.sleep(1)

    print("\n" + "=" * 60)
    print("Replacing slot 0 with KlonCentaur (by name)")
    print("=" * 60)

    rack[0] = "KlonCentaur"
    print(rack)

    time.sleep(1)

    # Або можна використовувати PluginConfig напряму
    print("\n" + "=" * 60)
    print("Adding BigMuffPi to slot 2 (via PluginConfig)")
    print("=" * 60)

    bigmuff = config.get_plugin_by_name("BigMuffPi")
    if bigmuff:
        rack[2] = bigmuff
    print(rack)

    time.sleep(1)

    # Показати плагіни за категорією
    print("\n" + "=" * 60)
    print("Distortion plugins:")
    for p in rack.get_plugins_by_category("distortion"):
        print(f"  - {p.name}: {p.uri}")

    time.sleep(1)

    print("\n" + "=" * 60)
    print("Clearing all")
    print("=" * 60)

    rack.clear()
    print(rack)
