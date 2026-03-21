import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Dict

class ModDatabase:
    def __init__(self, db_path: str):
        """Initialize database connection and create tables if needed."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mods (
                    workshop_id TEXT PRIMARY KEY,
                    app_id INTEGER NOT NULL,
                    title TEXT NULL,
                    content_path TEXT NOT NULL,
                    last_downloaded_at INTEGER NOT NULL,
                    remote_updated_at INTEGER NULL,
                    status TEXT NOT NULL,
                    last_error TEXT NULL
                )
            """)
            conn.commit()
            logging.info(f"Database initialized at {self.db_path}")

    def upsert_mod(self, workshop_id: str, app_id: int, content_path: str,
                   status: str, title: Optional[str] = None,
                   remote_updated_at: Optional[int] = None,
                   last_error: Optional[str] = None,
                   last_downloaded_at: Optional[int] = None) -> bool:
        """
        Insert or update a mod record.

        Args:
            workshop_id: Steam Workshop ID
            app_id: App ID (281990 for Stellaris)
            content_path: User library path where mod is accessible (e.g., /path/to/library/<workshop_id>)
            status: "success" or "failed"
            title: Mod title (optional; preserved on update if None)
            remote_updated_at: Unix timestamp of remote update (optional; preserved on update if None)
            last_error: Error message if failed
            last_downloaded_at: Unix timestamp of last download

        Returns:
            bool: True if successful
        """
        if last_downloaded_at is None:
            import time
            last_downloaded_at = int(time.time())

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO mods
                    (workshop_id, app_id, title, content_path, last_downloaded_at,
                     remote_updated_at, status, last_error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workshop_id) DO UPDATE SET
                        app_id=excluded.app_id,
                        title=COALESCE(excluded.title, mods.title),
                        content_path=excluded.content_path,
                        last_downloaded_at=excluded.last_downloaded_at,
                        remote_updated_at=COALESCE(excluded.remote_updated_at, mods.remote_updated_at),
                        status=excluded.status,
                        last_error=excluded.last_error
                """, (
                    workshop_id,
                    app_id,
                    title,
                    content_path,
                    last_downloaded_at,
                    remote_updated_at,
                    status,
                    last_error
                ))
                conn.commit()
                logging.info(f"Upserted mod {workshop_id} with status {status}")
                return True
        except Exception as e:
            logging.error(f"Failed to upsert mod {workshop_id}: {str(e)}")
            return False

    def get_mod(self, workshop_id: str) -> Optional[Dict]:
        """Retrieve a single mod record."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM mods WHERE workshop_id = ?",
                    (workshop_id,)
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logging.error(f"Failed to get mod {workshop_id}: {str(e)}")
            return None

    def list_all_mods(self) -> List[Dict]:
        """Retrieve all mod records."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM mods ORDER BY workshop_id")
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logging.error(f"Failed to list mods: {str(e)}")
            return []

    def delete_mod(self, workshop_id: str) -> bool:
        """Delete a mod record."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM mods WHERE workshop_id = ?", (workshop_id,))
                conn.commit()
                logging.info(f"Deleted mod {workshop_id}")
                return True
        except Exception as e:
            logging.error(f"Failed to delete mod {workshop_id}: {str(e)}")
            return False
