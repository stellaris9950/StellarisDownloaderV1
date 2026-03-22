import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Callable, Optional

import requests

from core.runtime_paths import (
    get_install_root,
    get_update_downloads_dir,
    get_update_staging_dir,
)
from core.version import APP_EXE_NAME, GITHUB_OWNER, GITHUB_REPO, UPDATER_EXE_NAME, __version__


GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


@dataclass
class ReleaseInfo:
    version: str
    tag_name: str
    notes: str
    asset_name: str
    asset_url: str
    published_at: str
    html_url: str


class UpdateError(RuntimeError):
    pass


def get_current_version() -> str:
    return __version__


def normalize_version_tag(tag: str) -> str:
    tag = (tag or "").strip()
    if tag.lower().startswith("v"):
        return tag[1:]
    return tag


def _version_parts(version: str) -> tuple[int, ...]:
    normalized = normalize_version_tag(version)
    matches = re.findall(r"\d+", normalized)
    if not matches:
        return (0,)
    return tuple(int(part) for part in matches)


def compare_versions(current_version: str, other_version: str) -> int:
    current_parts = _version_parts(current_version)
    other_parts = _version_parts(other_version)
    for current_part, other_part in zip_longest(current_parts, other_parts, fillvalue=0):
        if current_part < other_part:
            return -1
        if current_part > other_part:
            return 1
    return 0


def _choose_windows_asset(release_data: dict) -> tuple[str, str]:
    assets = release_data.get("assets") or []
    if not assets:
        raise UpdateError("No release assets were found.")

    candidates = []
    for asset in assets:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url") or ""
        lowered = name.lower()
        if not url or not lowered.endswith(".zip"):
            continue
        score = 0
        if "windows" in lowered or "win" in lowered:
            score += 2
        if "stellarismodmanager" in lowered:
            score += 2
        if "stellarisdownloaderv1" in lowered:
            score += 1
        if lowered == "stellarismodmanager.zip":
            score += 4
        if lowered.startswith("stellarismodmanager"):
            score += 2
        if "portable" in lowered:
            score -= 1
        candidates.append((score, name, url))

    if not candidates:
        raise UpdateError("No Windows zip asset was found in the latest release.")

    candidates.sort(key=lambda item: (item[0], item[1].lower()), reverse=True)
    _, asset_name, asset_url = candidates[0]
    return asset_name, asset_url


def fetch_latest_release_info() -> ReleaseInfo:
    try:
        response = requests.get(
            GITHUB_LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "StellarisModManager-Updater",
            },
            timeout=15,
        )
        response.raise_for_status()
        release_data = response.json()
    except requests.RequestException as exc:
        raise UpdateError(f"Failed to check GitHub releases: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(f"GitHub returned malformed release data: {exc}") from exc

    tag_name = release_data.get("tag_name")
    if not tag_name:
        raise UpdateError("Latest GitHub release did not contain a tag name.")

    asset_name, asset_url = _choose_windows_asset(release_data)
    return ReleaseInfo(
        version=normalize_version_tag(tag_name),
        tag_name=tag_name,
        notes=(release_data.get("body") or "").strip(),
        asset_name=asset_name,
        asset_url=asset_url,
        published_at=release_data.get("published_at") or "",
        html_url=release_data.get("html_url") or "",
    )


def check_for_updates() -> dict:
    release = fetch_latest_release_info()
    current_version = get_current_version()
    comparison = compare_versions(current_version, release.version)
    return {
        "current_version": current_version,
        "latest_version": release.version,
        "update_available": comparison < 0,
        "release": release,
    }


def download_release_asset(
    release: ReleaseInfo,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Path:
    destination = get_update_downloads_dir() / release.asset_name

    try:
        with requests.get(
            release.asset_url,
            headers={"User-Agent": "StellarisModManager-Updater"},
            stream=True,
            timeout=60,
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(destination, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    output_file.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
    except requests.RequestException as exc:
        if destination.exists():
            destination.unlink(missing_ok=True)
        raise UpdateError(f"Failed to download update package: {exc}") from exc

    if not destination.exists() or destination.stat().st_size == 0:
        raise UpdateError("Downloaded update package is missing or empty.")

    return destination


def launch_updater_for_package(package_path: Path) -> None:
    install_root = get_install_root()
    helper_source = install_root / UPDATER_EXE_NAME
    if not helper_source.exists():
        raise UpdateError(f"Updater helper was not found at {helper_source}")

    staging_root = get_update_staging_dir() / f"update-{int(time.time())}"
    staging_root.mkdir(parents=True, exist_ok=True)
    helper_runtime_path = staging_root / UPDATER_EXE_NAME
    shutil.copy2(helper_source, helper_runtime_path)

    app_executable = Path(sys.executable).resolve()
    arguments = [
        str(helper_runtime_path),
        "--parent-pid",
        str(os.getpid()),
        "--zip",
        str(package_path),
        "--install-dir",
        str(install_root),
        "--app-exe-name",
        APP_EXE_NAME,
    ]

    try:
        subprocess.Popen(arguments, cwd=str(staging_root))
    except OSError as exc:
        raise UpdateError(f"Failed to launch updater helper: {exc}") from exc

    logging.info("Launched updater helper from %s for %s", helper_runtime_path, app_executable)
