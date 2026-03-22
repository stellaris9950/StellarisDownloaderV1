import logging
import os
import sys
from pathlib import Path


APP_DIR_NAME = "StellarisModManager"
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_bundle_root() -> Path:
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def get_user_data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    root = base / APP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_logs_dir() -> Path:
    path = get_user_data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_log_file_path() -> Path:
    return get_logs_dir() / "app.log"


def get_data_dir() -> Path:
    path = get_user_data_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> str:
    return str(get_data_dir() / "app.db")


def get_settings_path() -> str:
    return str(get_data_dir() / "settings.json")


def get_bundled_steamcmd_root() -> Path:
    return get_bundle_root() / "steamcmd"


def _ensure_runtime_steamcmd_layout(runtime_root: Path) -> None:
    for relative_path in (
        "appcache",
        "config",
        "depotcache",
        "logs",
        "package",
        "public",
        "siteserverui",
        "steamapps",
        "steamapps/workshop",
        "steamapps/workshop/content",
        "steamapps/workshop/downloads",
        "steamapps/workshop/temp",
        "userdata",
        "userdata/anonymous",
        "userdata/anonymous/config",
    ):
        (runtime_root / relative_path).mkdir(parents=True, exist_ok=True)


def get_runtime_steamcmd_root() -> Path:
    if not is_frozen():
        return get_bundled_steamcmd_root()

    runtime_root = get_user_data_root() / "steamcmd"
    bundled_root = get_bundled_steamcmd_root()
    runtime_root.mkdir(parents=True, exist_ok=True)

    _ensure_runtime_steamcmd_layout(runtime_root)

    bundled_exe = bundled_root / "steamcmd.exe"
    runtime_exe = runtime_root / "steamcmd.exe"
    if bundled_exe.exists() and not runtime_exe.exists():
        runtime_exe.write_bytes(bundled_exe.read_bytes())

    return runtime_root


def configure_logging() -> None:
    log_file = get_log_file_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
