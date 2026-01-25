"""
PySide6 UI for MODEP Rack control.

Run with: python qrack.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


from mod_rack.config import Config
from mod_rack.rack import Orchestrator, OrchestratorMode


def main():
    # Load config

    # Override server URL if needed
    import argparse

    parser = argparse.ArgumentParser(description="MODEP Rack Controller")
    parser.add_argument("--server", "-s", default=None, help="MOD server URL")
    parser.add_argument(
        "--config", "-c", help="Config", type=Path, default="config.toml"
    )
    parser.add_argument("--master", "-m", help="Master", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.config)

    if args.server:
        config.server.url = args.server

    # Create rack (do not force reset on init — build state from WebSocket)
    print("Connecting to MOD server...")
    try:
        Orchestrator(config, OrchestratorMode.MANAGER).run()    
    except KeyboardInterrupt:
        print("Stopping monitor...")


if __name__ == "__main__":
    main()
