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


@dataclass
class HardwareConfig:
    inputs: list[str] = field(default_factory=lambda: ["capture_1", "capture_2"])
    outputs: list[str] = field(default_factory=lambda: ["playback_1", "playback_2"])


@dataclass
class ServerConfig:
    url: str = "http://127.0.0.1:18181"


@dataclass
class RigConfig:
    slot_count: int = 4


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
        hardware = HardwareConfig(**data.get("hardware", {}))
        rig = RigConfig(**data.get("rig", {}))

        plugins = [PluginConfig(**p) for p in data.get("plugins", [])]

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

    def get_plugins_by_category(self, category: str) -> list[PluginConfig]:
        """Отримати всі плагіни певної категорії"""
        category_lower = category.lower()
        return [p for p in self.plugins if p.category.lower() == category_lower]

    def list_categories(self) -> list[str]:
        """Отримати список всіх категорій"""
        return sorted(set(p.category for p in self.plugins if p.category))
