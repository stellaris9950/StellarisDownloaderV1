import logging
from typing import Optional, Dict, List
from core.workshop_api import fetch_mod_metadata

def check_mod_for_updates(workshop_id: str, stored_remote_updated_at: Optional[int]) -> Dict:
    """
    Check if a single mod has updates available.

    Args:
        workshop_id: Steam Workshop ID
        stored_remote_updated_at: Stored timestamp from last successful download

    Returns:
        Dict with keys:
            - workshop_id: str
            - stored_remote_updated_at: Optional[int]
            - latest_remote_updated_at: Optional[int]
            - status: "up_to_date" | "update_available" | "failed_check"
            - latest_title: Optional[str]
            - error: Optional[str]
    """
    try:
        metadata = fetch_mod_metadata(workshop_id)
        
        if not metadata:
            return {
                "workshop_id": workshop_id,
                "stored_remote_updated_at": stored_remote_updated_at,
                "latest_remote_updated_at": None,
                "latest_title": None,
                "status": "failed_check",
                "error": "Failed to fetch metadata from Steam API"
            }
        
        latest_remote_updated_at = metadata.get("remote_updated_at")
        latest_title = metadata.get("title")
        
        # Determine update status
        if stored_remote_updated_at is None:
            # Never checked/downloaded before, treat as up_to_date (no update to check)
            status = "up_to_date"
        elif latest_remote_updated_at is None:
            status = "failed_check"
            error_msg = "Latest metadata missing remote_updated_at"
        elif latest_remote_updated_at > stored_remote_updated_at:
            status = "update_available"
            error_msg = None
        else:
            status = "up_to_date"
            error_msg = None
        
        return {
            "workshop_id": workshop_id,
            "stored_remote_updated_at": stored_remote_updated_at,
            "latest_remote_updated_at": latest_remote_updated_at,
            "latest_title": latest_title,
            "status": status,
            "error": error_msg if status == "failed_check" else None
        }
    except Exception as e:
        logging.error(f"Unexpected error checking updates for {workshop_id}: {str(e)}")
        return {
            "workshop_id": workshop_id,
            "stored_remote_updated_at": stored_remote_updated_at,
            "latest_remote_updated_at": None,
            "latest_title": None,
            "status": "failed_check",
            "error": str(e)
        }

def check_all_mods_for_updates(mods: List[Dict]) -> List[Dict]:
    """
    Check all mods for updates.

    Args:
        mods: List of mod records from database

    Returns:
        List of update check results
    """
    results = []
    for mod in mods:
        workshop_id = mod['workshop_id']
        stored_remote_updated_at = mod.get('remote_updated_at')
        
        result = check_mod_for_updates(workshop_id, stored_remote_updated_at)
        results.append(result)
    
    return results
