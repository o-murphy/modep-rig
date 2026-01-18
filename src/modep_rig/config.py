from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # pip install tomli for Python < 3.11


__all__ = [
    "PluginConfig",
    "HardwareConfig",
    "ServerConfig",
    "RigConfig",
    "Config",
]


@dataclass
class PluginConfig:
    name: str
    uri: str
    category: str = ""
    # Опціональні override для портів (для моно/стерео конверсії)
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    # Явний режим каналів: "mono", "stereo", або None (авто)
    mode: str | None = None
    # All-to-all routing: з'єднати всі входи/виходи між собою
    join_inputs: bool = False
    join_outputs: bool = False


@dataclass
class HardwareConfig:
    # None = auto-detect from MOD-UI, list = override with specific ports
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    # All-to-all routing for hardware ports
    join_inputs: bool = False   # Join all hardware inputs to first plugin
    join_outputs: bool = False  # Join last plugin outputs to all hardware outputs


@dataclass
class ServerConfig:
    url: str = "http://127.0.0.1:18181"


@dataclass
class RigConfig:
    # Maximum number of slots allowed (None = unlimited)
    slots_limit: int | None = None


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    rig: RigConfig = field(default_factory=RigConfig)
    plugins: list[PluginConfig] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> "Config":
        """Завантажує конфігурацію з TOML файлу"""
        path = Path(path)

        if not path.exists():
            print(f"Config file {path} not found, using defaults")
            return cls()

        with open(path, "rb") as f:
            data = tomllib.load(f)

        server = ServerConfig(**data.get("server", {}))
        hw_data = data.get("hardware", {})
        hardware = HardwareConfig(
            inputs=hw_data.get("inputs"),  # None = auto-detect
            outputs=hw_data.get("outputs"),  # None = auto-detect
            join_inputs=hw_data.get("join_inputs", False),
            join_outputs=hw_data.get("join_outputs", False),
        )
        rig_data = data.get("rig", {})
        rig = RigConfig(
            slots_limit=rig_data.get("slots_limit") or rig_data.get("slot_count"),  # backward compat
        )

        plugins = []
        for p in data.get("plugins", []):
            plugins.append(PluginConfig(
                name=p["name"],
                uri=p["uri"],
                category=p.get("category", ""),
                inputs=p.get("inputs"),
                outputs=p.get("outputs"),
                mode=p.get("mode"),
                join_inputs=p.get("join_inputs", False),
                join_outputs=p.get("join_outputs", False),
            ))

        return cls(
            server=server,
            hardware=hardware,
            rig=rig,
            plugins=plugins,
        )

    def get_plugin_by_name(self, name: str) -> PluginConfig | None:
        """Знайти плагін за ім'ям (case-insensitive)"""
        name_lower = name.lower()
        for plugin in self.plugins:
            if plugin.name.lower() == name_lower:
                return plugin
        return None

    def get_plugin_by_uri(self, uri: str) -> PluginConfig | None:
        """Знайти плагін за URI"""
        for plugin in self.plugins:
            if plugin.uri == uri:
                return plugin
        return None

    def is_supported(self, uri: str) -> bool:
        """Перевірити чи плагін підтримується (є в конфігу)"""
        return self.get_plugin_by_uri(uri) is not None

    def get_plugins_by_category(self, category: str) -> list[PluginConfig]:
        """Отримати всі плагіни певної категорії"""
        category_lower = category.lower()
        return [p for p in self.plugins if p.category.lower() == category_lower]

    def list_categories(self) -> list[str]:
        """Отримати список всіх категорій"""
        return sorted(set(p.category for p in self.plugins if p.category))
