import requests
import logging
from typing import Optional, Dict

STEAM_API_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1"

def fetch_mod_metadata(workshop_id: str) -> Optional[Dict]:
    """
    Fetch metadata for a mod from Steam Web API.

    Args:
        workshop_id: Steam Workshop ID

    Returns:
        Optional[Dict]: Dictionary with mod metadata, or None if fetch fails.
    """
    try:
        data = {
            "itemcount": 1,
            "publishedfileids[0]": workshop_id
        }
        
        logging.info(f"Fetching metadata for workshop ID {workshop_id}")
        response = requests.post(STEAM_API_URL, data=data, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        response_data = data.get("response", {})

        publishedfiledetails = response_data.get("publishedfiledetails")
        if not isinstance(publishedfiledetails, list) or len(publishedfiledetails) == 0:
            logging.warning(f"No publishedfiledetails returned for {workshop_id}")
            return None

        file_details = publishedfiledetails[0]
        if not isinstance(file_details, dict) or file_details.get("result") != 1:
            logging.warning(f"Invalid or failed file details result for {workshop_id}: {file_details}")
            return None

        # Extract available metadata
        metadata = {
            "title": file_details.get("title"),
            "description": file_details.get("description"),
            "preview_url": file_details.get("preview_url"),
            "creator": file_details.get("creator"),
            "remote_updated_at": file_details.get("time_updated"),
            "time_created": file_details.get("time_created"),
            "tags": file_details.get("tags", []),
            "file_size": file_details.get("file_size"),
        }
        
        # Validate required fields
        if metadata["title"] is None or metadata["remote_updated_at"] is None:
            logging.warning(f"Missing required fields for {workshop_id}")
            return None
        
        # Convert timestamps to int
        if metadata["remote_updated_at"]:
            metadata["remote_updated_at"] = int(metadata["remote_updated_at"])
        if metadata["time_created"]:
            metadata["time_created"] = int(metadata["time_created"])
        
        logging.info(f"Successfully fetched metadata for {workshop_id}: title='{metadata['title']}'")
        
        return metadata
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error fetching metadata for {workshop_id}: {str(e)}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error fetching metadata for {workshop_id}: {str(e)}")
        return None
