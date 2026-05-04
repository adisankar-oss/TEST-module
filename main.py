from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
BACKEND_MAIN = BACKEND_DIR / "main.py"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

spec = importlib.util.spec_from_file_location("backend_app_main", BACKEND_MAIN)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load backend app from {BACKEND_MAIN}")

backend_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend_module)

app = backend_module.app
