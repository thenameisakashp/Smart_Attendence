"""Start the Flask dashboard without leaving a console window open."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "dataset" / "launch_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
log_path = LOG_DIR / f"dashboard_{stamp}.log"

with log_path.open("w", encoding="utf-8", errors="replace") as output:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    python_exe = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    subprocess.Popen(
        [str(python_exe if python_exe.is_file() else sys.executable), str(BASE_DIR / "app.py")],
        cwd=str(BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
