"""
Convenience launcher. Run this instead of uvicorn directly.
Always executes from project root regardless of shell CWD.
"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).parent
cmd = [
    sys.executable,
    "-m",
    "uvicorn",
    "backend.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
    "--reload",
    "--reload-dir",
    str(root / "backend"),
]
subprocess.run(cmd, cwd=str(root))