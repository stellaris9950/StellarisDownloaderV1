import argparse
import ctypes
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def configure_helper_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )


def show_message(title: str, message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        pass


def wait_for_process_exit(pid: int, timeout_seconds: int = 120) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        process_handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
        if not process_handle:
            return
        ctypes.windll.kernel32.CloseHandle(process_handle)
        time.sleep(0.5)

    raise RuntimeError("Timed out while waiting for the main app to exit.")


def extract_zip_package(zip_path: Path, extract_root: Path) -> Path:
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(extract_root)

    return extract_root


def find_staged_app_dir(extract_root: Path, app_exe_name: str) -> Path:
    direct_candidate = extract_root / app_exe_name
    if direct_candidate.exists():
        return extract_root

    for candidate in extract_root.rglob(app_exe_name):
        return candidate.parent

    raise RuntimeError(f"Could not find {app_exe_name} inside the update package.")


def replace_installation(install_dir: Path, staged_dir: Path) -> None:
    backup_dir = install_dir.with_name(f"{install_dir.name}.backup")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    install_parent = install_dir.parent
    install_parent.mkdir(parents=True, exist_ok=True)

    if install_dir.exists():
        install_dir.rename(backup_dir)

    try:
        shutil.copytree(staged_dir, install_dir)
    except Exception:
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        if backup_dir.exists():
            backup_dir.rename(install_dir)
        raise

    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def restart_app(install_dir: Path, app_exe_name: str) -> None:
    app_path = install_dir / app_exe_name
    if not app_path.exists():
        raise RuntimeError(f"Updated app executable not found at {app_path}")

    result = ctypes.windll.shell32.ShellExecuteW(None, "open", str(app_path), None, str(install_dir), 1)
    if result <= 32:
        raise RuntimeError(f"Failed to restart the updated app (ShellExecute result: {result}).")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--app-exe-name", required=True)
    args = parser.parse_args()

    install_dir = Path(args.install_dir).resolve()
    zip_path = Path(args.zip).resolve()
    log_path = zip_path.parent / "updater.log"
    configure_helper_logging(log_path)

    try:
        logging.info("Updater helper started.")
        wait_for_process_exit(args.parent_pid)
        if not zip_path.exists():
            raise RuntimeError(f"Update package not found: {zip_path}")

        with tempfile.TemporaryDirectory(prefix="stellaris-update-") as temp_dir:
            extract_root = Path(temp_dir) / "extracted"
            extract_root.mkdir(parents=True, exist_ok=True)
            extract_zip_package(zip_path, extract_root)
            staged_dir = find_staged_app_dir(extract_root, args.app_exe_name)
            replace_installation(install_dir, staged_dir)

        restart_app(install_dir, args.app_exe_name)
        logging.info("Updater helper completed successfully.")
        return 0
    except Exception as exc:
        logging.exception("Updater helper failed")
        show_message("Update Failed", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
