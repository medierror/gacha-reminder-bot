"""Entry point for hosts that start main.py by default (e.g. bot-hosting.net). Same as running bot.py."""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
runpy.run_path(str(HERE / "bot.py"), run_name="__main__")
