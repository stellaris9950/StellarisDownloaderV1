import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.database import ModDatabase
from core.settings import SettingsManager
from core.workshop_api import fetch_mod_metadata

APP_ID = 281990


def get_steamcmd_root() -> Path:
    return Path(__file__).resolve().parent.parent / "steamcmd"


def get_workshop_content_root() -> Path:
    return get_steamcmd_root() / "steamapps" / "workshop" / "content"


def get_junction_path() -> Path:
    return get_workshop_content_root() / str(APP_ID)


def normalize_library_root(library_root: str) -> Path:
    return Path(library_root).expanduser().resolve()


def _run_command(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def is_junction(path: Path) -> bool:
    if not path.exists():
        return False
    result = _run_command(["fsutil", "reparsepoint", "query", str(path)])
    return result.returncode == 0


def get_junction_target(path: Path) -> Optional[Path]:
    if not is_junction(path):
        return None
    return path.resolve()


def remove_junction(path: Path) -> None:
    if not path.exists():
        return
    if not is_junction(path):
        raise ValueError(f"Refusing to remove non-junction path: {path}")

    result = _run_command(["cmd", "/c", "rmdir", str(path)])
    if result.returncode != 0 or path.exists():
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to remove junction {path}: {stderr}")


def create_junction(link_path: Path, target_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.mkdir(parents=True, exist_ok=True)

    result = _run_command(["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)])
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to create junction {link_path} -> {target_path}: {stderr}")


def ensure_junction_target(library_root: str) -> Path:
    target_path = normalize_library_root(library_root)
    target_path.mkdir(parents=True, exist_ok=True)

    junction_path = get_junction_path()
    if junction_path.exists():
        if is_junction(junction_path):
            current_target = junction_path.resolve()
            if current_target != target_path:
                raise ValueError(
                    f"Existing SteamCMD junction points to {current_target}, expected {target_path}."
                )
            return junction_path

        if not junction_path.is_dir():
            raise ValueError(f"SteamCMD workshop path exists but is not a directory: {junction_path}")

        if any(junction_path.iterdir()):
            raise ValueError(
                f"SteamCMD workshop path is a non-empty normal directory: {junction_path}"
            )

        junction_path.rmdir()

    create_junction(junction_path, target_path)
    verified_target = get_junction_target(junction_path)
    if verified_target != target_path:
        raise RuntimeError(
            f"Created SteamCMD junction but verification failed: {junction_path} -> {verified_target}"
        )
    return junction_path


def validate_library_root(library_root: Optional[str]) -> Tuple[bool, str]:
    if not library_root:
        return False, "Library root is not configured."

    try:
        root_path = normalize_library_root(library_root)
    except Exception as exc:
        return False, f"Library root is invalid: {exc}"

    if not root_path.exists():
        return False, "Library root does not exist."

    if not root_path.is_dir():
        return False, "Library root is not a directory."

    junction_path = get_junction_path()
    if not junction_path.exists():
        return True, ""

    if is_junction(junction_path):
        current_target = junction_path.resolve()
        if current_target != root_path:
            return False, (
                f"SteamCMD junction points to {current_target}, not the configured library root."
            )
        return True, ""

    if junction_path.is_dir() and any(junction_path.iterdir()):
        return False, "SteamCMD workshop path is occupied by a non-empty normal directory."

    if junction_path.is_file():
        return False, "SteamCMD workshop path is blocked by a file."

    return True, ""


def build_import_records(library_root: str) -> List[Dict]:
    root_path = normalize_library_root(library_root)
    if not root_path.exists():
        return []

    records: List[Dict] = []
    for child in sorted(root_path.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or not child.name.isdigit():
            continue

        workshop_id = child.name
        last_downloaded_at = int(child.stat().st_mtime)
        metadata = fetch_mod_metadata(workshop_id) or {}

        records.append({
            "workshop_id": workshop_id,
            "app_id": APP_ID,
            "title": metadata.get("title"),
            "description": metadata.get("description"),
            "preview_url": metadata.get("preview_url"),
            "creator": metadata.get("creator"),
            "time_created": metadata.get("time_created"),
            "content_path": str(child),
            "last_downloaded_at": last_downloaded_at,
            "remote_updated_at": metadata.get("remote_updated_at"),
            "file_size": metadata.get("file_size"),
            "status": "success",
            "last_error": None,
        })

    return records


def rebuild_database_from_library_root(db_path: str, library_root: str) -> Dict:
    records = build_import_records(library_root)
    db = ModDatabase(db_path)
    if not db.replace_all_mods(records):
        raise RuntimeError("Failed to rebuild database from the selected library root.")
    return {
        "imported_count": len(records),
        "records": records,
    }


def switch_library_root(settings_path: str, db_path: str, new_library_root: str) -> Dict:
    settings = SettingsManager(settings_path)
    previous_root_raw = settings.get_library_root()
    previous_root = normalize_library_root(previous_root_raw) if previous_root_raw else None
    new_root = normalize_library_root(new_library_root)
    new_root.mkdir(parents=True, exist_ok=True)

    junction_path = get_junction_path()
    previous_junction_target = get_junction_target(junction_path)

    if previous_root and previous_root == new_root:
        ensure_junction_target(str(new_root))
        rebuild_result = rebuild_database_from_library_root(db_path, str(new_root))
        settings.set_library_root(str(new_root))
        return {
            "library_root": str(new_root),
            "imported_count": rebuild_result["imported_count"],
            "changed": False,
        }

    if junction_path.exists() and not is_junction(junction_path):
        if not junction_path.is_dir() or any(junction_path.iterdir()):
            raise RuntimeError(
                f"Cannot replace SteamCMD path because it is not a removable junction: {junction_path}"
            )
        junction_path.rmdir()

    try:
        if junction_path.exists():
            remove_junction(junction_path)

        create_junction(junction_path, new_root)
        verified_target = get_junction_target(junction_path)
        if verified_target != new_root:
            raise RuntimeError(
                f"Junction verification failed after switch: {junction_path} -> {verified_target}"
            )

        rebuild_result = rebuild_database_from_library_root(db_path, str(new_root))
        settings.set_library_root(str(new_root))
        return {
            "library_root": str(new_root),
            "imported_count": rebuild_result["imported_count"],
            "changed": True,
        }
    except Exception:
        logging.exception("Library root switch failed; attempting rollback")
        try:
            if junction_path.exists() and is_junction(junction_path):
                remove_junction(junction_path)
        except Exception:
            logging.exception("Failed to remove new junction during rollback")

        if previous_junction_target:
            try:
                create_junction(junction_path, previous_junction_target)
            except Exception:
                logging.exception("Failed to restore previous junction during rollback")

        raise
