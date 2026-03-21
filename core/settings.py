import json
import logging
from pathlib import Path
from typing import Optional, Dict

class SettingsManager:
    """Manage persistent application settings via JSON."""
    
    def __init__(self, settings_path: str):
        """
        Initialize settings manager.
        
        Args:
            settings_path: Path to settings.json file
        """
        self.settings_path = Path(settings_path)
        self._ensure_settings_file()
    
    def _ensure_settings_file(self):
        """Create settings file if it doesn't exist."""
        if not self.settings_path.exists():
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._save_settings({})
    
    def _load_settings(self) -> Dict:
        """Load settings from JSON file."""
        try:
            if self.settings_path.exists():
                with open(self.settings_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logging.error(f"Error loading settings: {e}")
        return {}
    
    def _save_settings(self, settings: Dict) -> None:
        """Save settings to JSON file."""
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_path, 'w') as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving settings: {e}")
            raise
    
    def get_library_root(self) -> Optional[str]:
        """Get stored library root."""
        settings = self._load_settings()
        return settings.get('library_root')
    
    def set_library_root(self, library_root: str) -> None:
        """Set library root and save."""
        # Validate path exists
        path = Path(library_root)
        if not path.exists():
            raise ValueError(f"Library root path does not exist: {library_root}")
        
        settings = self._load_settings()
        settings['library_root'] = str(path.resolve())
        self._save_settings(settings)
        logging.info(f"Library root set to: {settings['library_root']}")
    
    def get_all_settings(self) -> Dict:
        """Get all settings."""
        return self._load_settings()
    
    def clear_settings(self) -> None:
        """Clear all settings."""
        self._save_settings({})
        logging.info("All settings cleared")
