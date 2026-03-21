import subprocess
import logging
import time
from pathlib import Path
from core.database import ModDatabase
from core.workshop_api import fetch_mod_metadata

def classify_steamcmd_output(output: str) -> str:
    """
    Classifies SteamCMD output as success or failed based on key phrases.

    Args:
        output (str): Combined stdout and stderr from SteamCMD.

    Returns:
        str: "success" or "failed"
    """
    output_lower = output.lower()
    
    success_phrases = ["success. downloaded item", "downloaded item"]
    failure_phrases = ["error", "failed", "timeout", "not downloaded"]
    
    for phrase in success_phrases:
        if phrase in output_lower:
            return "success"
    
    for phrase in failure_phrases:
        if phrase in output_lower:
            return "failed"
    
    return "failed"  # Default to failed if no clear indicators

def download_mod(workshop_id: str, download_root: str, db_path: str) -> dict:
    """
    Downloads a Stellaris mod from Steam Workshop using SteamCMD and tracks it in SQLite.

    Args:
        workshop_id (str): The Steam Workshop ID of the mod to download.
        download_root (str): The root directory where the mod should be exported.
        db_path (str): Path to the SQLite database file.

    Returns:
        dict: Structured result with status and details.
    """
    # Validate workshop_id is numeric
    if not workshop_id.isdigit():
        return {
            "status": "failed",
            "workshop_id": workshop_id,
            "content_path": "",
            "final_path": None,
            "folder_exists": False,
            "folder_nonempty": False,
            "copied_successfully": False,
            "junction_created": False,
            "junction_verified": False,
            "junction_path": "",
            "library_target_path": download_root,
            "title": None,
            "remote_updated_at": None,
            "return_code": -1,
            "stdout": "",
            "stderr": "",
            "error": "Invalid workshop_id: must be numeric."
        }

    # Dynamically find project root and bundled SteamCMD executable
    steamcmd_root = Path(__file__).resolve().parent.parent / "steamcmd"
    steamcmd_executable = steamcmd_root / "steamcmd.exe"
    content_path = steamcmd_root / "steamapps" / "workshop" / "content" / "281990" / workshop_id

    if not steamcmd_executable.exists():
        return {
            "status": "failed",
            "workshop_id": workshop_id,
            "return_code": -1,
            "stdout": "",
            "stderr": "",
            "content_path": str(content_path),
            "folder_exists": False,
            "folder_nonempty": False,
            "error": f"SteamCMD executable not found at {steamcmd_executable}"
        }

    cmd = [
        str(steamcmd_executable),
        "+login", "anonymous",
        "+workshop_download_item", "281990", workshop_id,
        "+quit"
    ]

    # Ensure library root exists
    Path(download_root).mkdir(parents=True, exist_ok=True)

    try:
        # Set up junction to library root
        content_281990 = content_path.parent
        junction_created = False
        junction_verified = False

        if content_281990.exists():
            if content_281990.is_dir():
                # Check if it's already a junction
                verify_cmd = ['fsutil', 'reparsepoint', 'query', str(content_281990)]
                verify_result = subprocess.run(verify_cmd, capture_output=True, text=True)
                if verify_result.returncode == 0:
                    # It's a junction
                    junction_verified = True

                    # Verify it points at the requested library target
                    current_target = content_281990.resolve()
                    requested_target = Path(download_root).resolve()
                    if current_target != requested_target:
                        return {
                            "status": "failed",
                            "workshop_id": workshop_id,
                            "content_path": str(content_path),
                            "final_path": None,
                            "folder_exists": False,
                            "folder_nonempty": False,
                            "copied_successfully": False,
                            "junction_created": False,
                            "junction_verified": False,
                            "junction_path": str(content_281990),
                            "library_target_path": download_root,
                            "title": None,
                            "remote_updated_at": None,
                            "return_code": -1,
                            "stdout": "",
                            "stderr": "",
                            "error": f"Existing junction at {content_281990} points to {current_target}, expected {requested_target}."
                        }
                else:
                    # Normal directory
                    if any(content_281990.iterdir()):
                        return {
                            "status": "failed",
                            "workshop_id": workshop_id,
                            "content_path": str(content_path),
                            "final_path": None,
                            "folder_exists": False,
                            "folder_nonempty": False,
                            "copied_successfully": False,
                            "junction_created": junction_created,
                            "junction_verified": junction_verified,
                            "junction_path": str(content_281990),
                            "library_target_path": download_root,
                            "return_code": -1,
                            "stdout": "",
                            "stderr": "",
                            "error": "Existing non-empty SteamCMD cache folder found. Remove or migrate it before creating junction."
                        }
                    else:
                        # Empty, remove and create junction
                        content_281990.rmdir()
                        junction_created = True
                        junction_cmd = ['cmd', '/c', 'mklink', '/J', str(content_281990), download_root]
                        junction_result = subprocess.run(junction_cmd, capture_output=True, text=True)
                        if junction_result.returncode != 0:
                            return {
                                "status": "failed",
                                "workshop_id": workshop_id,
                                "content_path": str(content_path),
                                "final_path": None,
                                "folder_exists": False,
                                "folder_nonempty": False,
                                "copied_successfully": False,
                                "junction_created": junction_created,
                                "junction_verified": junction_verified,
                                "junction_path": str(content_281990),
                                "library_target_path": download_root,
                                "return_code": -1,
                                "stdout": "",
                                "stderr": "",
                                "error": f"Failed to create junction: {junction_result.stderr}"
                            }
                        # Verify
                        verify_result = subprocess.run(verify_cmd, capture_output=True, text=True)
                        junction_verified = verify_result.returncode == 0
            else:
                # Exists but not dir? Unlikely, fail
                return {
                    "status": "failed",
                    "workshop_id": workshop_id,
                    "content_path": str(content_path),
                    "final_path": None,
                    "folder_exists": False,
                    "folder_nonempty": False,
                    "copied_successfully": False,
                    "junction_created": junction_created,
                    "junction_verified": junction_verified,
                    "junction_path": str(content_281990),
                    "library_target_path": download_root,
                    "return_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "error": f"Path {content_281990} exists but is not a directory."
                }
        else:
            # Not exist, create junction
            junction_created = True
            junction_cmd = ['cmd', '/c', 'mklink', '/J', str(content_281990), download_root]
            junction_result = subprocess.run(junction_cmd, capture_output=True, text=True)
            if junction_result.returncode != 0:
                return {
                    "status": "failed",
                    "workshop_id": workshop_id,
                    "content_path": str(content_path),
                    "final_path": None,
                    "folder_exists": False,
                    "folder_nonempty": False,
                    "copied_successfully": False,
                    "junction_created": junction_created,
                    "junction_verified": junction_verified,
                    "junction_path": str(content_281990),
                    "library_target_path": download_root,
                    "return_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "error": f"Failed to create junction: {junction_result.stderr}"
                }
            # Verify
            verify_cmd = ['fsutil', 'reparsepoint', 'query', str(content_281990)]
            verify_result = subprocess.run(verify_cmd, capture_output=True, text=True)
            junction_verified = verify_result.returncode == 0

        # Run SteamCMD download
        logging.info(f"Starting download for workshop ID: {workshop_id} using {steamcmd_executable}")
        result = subprocess.run(cmd, capture_output=True, text=False)

        # Decode output safely
        stdout_text = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        output_combined = stdout_text + stderr_text

        # Classify output
        output_status = classify_steamcmd_output(output_combined)

        # Check folder
        folder_exists = content_path.exists() and content_path.is_dir()
        folder_nonempty = folder_exists and any(content_path.iterdir())

        # Determine final status
        status = "success" if output_status == "success" and folder_exists and folder_nonempty else "failed"

        error = None
        title = None
        remote_updated_at = None

        # Initialize database and store record
        db = ModDatabase(db_path)
        last_downloaded_at = int(time.time())

        if status == "success":
            # Fetch metadata from Steam API
            metadata = fetch_mod_metadata(workshop_id)
            if metadata:
                title = metadata.get("title")
                remote_updated_at = metadata.get("remote_updated_at")
            
            # Compute user library path
            user_library_path = Path(download_root) / workshop_id
            
            # Upsert to database
            db.upsert_mod(
                workshop_id=workshop_id,
                app_id=281990,
                content_path=str(user_library_path),
                status="success",
                title=title,
                remote_updated_at=remote_updated_at,
                last_downloaded_at=last_downloaded_at
            )
        else:
            # Extract specific error from output
            specific_error = "SteamCMD download or verification failed"
            if "error" in output_combined.lower():
                for line in output_combined.split('\n'):
                    if "error" in line.lower():
                        specific_error = line.strip()
                        break
            
            # Compute user library path
            user_library_path = Path(download_root) / workshop_id
            
            # Store failed attempt
            db.upsert_mod(
                workshop_id=workshop_id,
                app_id=281990,
                content_path=str(user_library_path),
                status="failed",
                last_error=specific_error,
                last_downloaded_at=last_downloaded_at
            )
            error = specific_error

        # Since using junction, user-facing path is the library path
        user_library_path = Path(download_root) / workshop_id
        final_path = str(user_library_path) if status == "success" else None
        copied_successfully = status == "success"

        logging.info(f"Download result for {workshop_id}: status={status}, output_status={output_status}, folder_exists={folder_exists}, folder_nonempty={folder_nonempty}")

        return {
            "status": status,
            "workshop_id": workshop_id,
            "content_path": str(user_library_path) if status == "success" else str(Path(download_root) / workshop_id),
            "final_path": final_path,
            "folder_exists": folder_exists,
            "folder_nonempty": folder_nonempty,
            "copied_successfully": copied_successfully,
            "junction_created": junction_created,
            "junction_verified": junction_verified,
            "junction_path": str(content_281990),
            "library_target_path": download_root,
            "title": title,
            "remote_updated_at": remote_updated_at,
            "return_code": result.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "error": error
        }
    except Exception as e:
        logging.error(f"Unexpected error downloading mod {workshop_id}: {str(e)}")
        try:
            db = ModDatabase(db_path)
            db.upsert_mod(
                workshop_id=workshop_id,
                app_id=281990,
                content_path=str(content_path),
                status="failed",
                last_error=str(e),
                last_downloaded_at=int(time.time())
            )
        except:
            pass  # Silent fail on db error during exception handling
        
        return {
            "status": "failed",
            "workshop_id": workshop_id,
            "content_path": str(content_path),
            "final_path": None,
            "folder_exists": False,
            "folder_nonempty": False,
            "copied_successfully": False,
            "junction_created": False,
            "junction_verified": False,
            "junction_path": str(content_path.parent),
            "library_target_path": download_root,
            "title": None,
            "remote_updated_at": None,
            "return_code": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e)
        }