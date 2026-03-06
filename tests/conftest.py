"""pytest configuration for astrbot_plugin_f1_notifier tests.

Inserts the repo root into sys.path so that
``from src.astrbot_plugin_f1_notifier import ...`` works without installing
the package.
"""

import sys
from pathlib import Path

# Repo root: tests/ → parent → repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
