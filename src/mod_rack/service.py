"""
PySide6 UI for MOD Rack control.

Run with: python qrack.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


from mod_rack.config import Config
from mod_rack.client import DEFAULT_SERVER_URL
from mod_rack.rack import Orchestrator, OrchestratorMode


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MOD Rack Controller")
    parser.add_argument(
        "--server", "-s", default=DEFAULT_SERVER_URL, help="MOD server URL"
    )
    parser.add_argument(
        "--config", "-c", help="Config", type=Path, default="config.toml"
    )
    args = parser.parse_args()

    config = Config.load(args.config)

    # Create rack (do not force reset on init — build state from WebSocket)
    print(f"Connecting to MOD server at {args.server}...")
    try:
        Orchestrator(args.server, config, OrchestratorMode.MANAGER).run()
    except KeyboardInterrupt:
        print("Stopping monitor...")


if __name__ == "__main__":
    main()
