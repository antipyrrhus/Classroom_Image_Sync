"""Windowless Task Scheduler entry point. Launch with .venv/Scripts/pythonw.exe."""

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import traceback


def main() -> int:
    root = Path(__file__).resolve().parent
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # pythonw has no standard streams. Capture console output and startup
    # failures here; the sync also keeps its usual timestamped sync.log.
    with (log_dir / "scheduled.log").open("a", encoding="utf-8", buffering=1) as output:
        with redirect_stdout(output), redirect_stderr(output):
            try:
                from classroom_image_sync import main as sync_main

                return sync_main(["--no-browser"])
            except Exception:
                traceback.print_exc()
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
