import argparse
import logging
import sys
import datetime
from pathlib import Path
from core.steamcmd import download_mod
from core.database import ModDatabase
from core.updater import check_all_mods_for_updates, update_mod, update_all_mods
from core.settings import SettingsManager

# Configure logging once for the whole app
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_db_path():
    """Get the default database path."""
    return str(Path(__file__).resolve().parent / "data" / "app.db")

def get_settings_path():
    """Get the default settings path."""
    return str(Path(__file__).resolve().parent / "data" / "settings.json")

def resolve_download_root(explicit_root: str = None) -> str:
    """
    Resolve download root with precedence: explicit > settings > fail.
    
    Args:
        explicit_root: Optional explicit download root from CLI
    
    Returns:
        Resolved download root path
    
    Raises:
        ValueError: If no root available
    """
    if explicit_root:
        return explicit_root
    
    settings = SettingsManager(get_settings_path())
    stored_root = settings.get_library_root()
    
    if stored_root:
        return stored_root
    
    raise ValueError(
        "No library root specified. Use --download-root to provide it, "
        "or run 'python app.py set-library-root <path>' to save a default."
    )

def cmd_download(args):
    """Handle download command."""
    try:
        download_root = resolve_download_root(getattr(args, 'download_root', None))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    print(f"Attempting to download mod with Workshop ID: {args.workshop_id}")
    print(f"Download root: {download_root}")
    
    result = download_mod(args.workshop_id, download_root, get_db_path())
    
    print(f"\nStatus: {result['status']}")
    print(f"Workshop ID: {result['workshop_id']}")
    print(f"Mod library path: {result['final_path'] or 'N/A'}")
    print(f"Database content path: {result['content_path'] or 'N/A'}")
    print(f"Title: {result.get('title') or 'N/A'}")
    print(f"Remote updated at: {result.get('remote_updated_at') or 'N/A'}")
    print(f"Junction verified: {result['junction_verified']}")
    
    if result["status"] == "success":
        print("\nDownload completed successfully.")
        sys.exit(0)
    else:
        print("\nDownload failed.")
        if result["error"]:
            print(f"Error: {result['error']}")
        sys.exit(1)

def cmd_list(args):
    """Handle list command."""
    db = ModDatabase(get_db_path())
    mods = db.list_all_mods()
    
    if not mods:
        print("No mods in database.")
        sys.exit(0)
    
    print(f"\n{'Workshop ID':<15} | {'Title':<35} | {'Status':<10} | {'Last Downloaded':<20} | {'Content Path':<50}")
    print("-" * 150)
    
    for mod in mods:
        workshop_id = mod['workshop_id']
        title = mod['title'] or "N/A"
        status = mod['status']
        last_downloaded = mod['last_downloaded_at'] or 0
        content_path = mod['content_path'] or "N/A"
        
        # Format timestamp
        if last_downloaded and last_downloaded > 0:
            dt = datetime.datetime.fromtimestamp(last_downloaded)
            last_downloaded_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_downloaded_str = "N/A"
        
        title_truncated = (title[:32] + "...") if len(title) > 35 else title
        path_truncated = (content_path[:47] + "...") if len(content_path) > 50 else content_path
        
        print(f"{workshop_id:<15} | {title_truncated:<35} | {status:<10} | {last_downloaded_str:<20} | {path_truncated:<50}")
    
    print(f"\nTotal mods: {len(mods)}")
    sys.exit(0)

def cmd_check_updates(args):
    """Handle check-updates command."""
    db = ModDatabase(get_db_path())
    mods = db.list_all_mods()
    
    if not mods:
        print("No mods in database.")
        sys.exit(0)
    
    print("\nChecking for updates...")
    results = check_all_mods_for_updates(mods)
    
    stats = {
        "up_to_date": 0,
        "update_available": 0,
        "failed_check": 0
    }
    
    print(f"\n{'Workshop ID':<15} | {'Title':<30} | {'Status':<18} | {'Stored Updated':<20} | {'Latest Updated':<20}")
    print("-" * 125)
    
    for result in results:
        workshop_id = result['workshop_id']
        title = result.get('latest_title') or "N/A"
        status = result['status']
        stored_ts = result['stored_remote_updated_at'] or 0
        latest_ts = result['latest_remote_updated_at'] or 0
        
        stats[status] += 1
        
        # Format timestamps
        if stored_ts and stored_ts > 0:
            stored_dt = datetime.datetime.fromtimestamp(stored_ts)
            stored_str = stored_dt.strftime("%Y-%m-%d %H:%M")
        else:
            stored_str = "N/A"
        
        if latest_ts and latest_ts > 0:
            latest_dt = datetime.datetime.fromtimestamp(latest_ts)
            latest_str = latest_dt.strftime("%Y-%m-%d %H:%M")
        else:
            latest_str = "N/A"
        
        title_truncated = (title[:27] + "...") if len(title) > 30 else title
        
        print(f"{workshop_id:<15} | {title_truncated:<30} | {status:<18} | {stored_str:<20} | {latest_str:<20}")
        
        if result.get('error'):
            print(f"  └─ Error: {result['error']}")
    
    print(f"\n{'Summary':<15} | {'Up to Date':<15} | {'Updates Available':<15} | {'Check Failed':<15}")
    print("-" * 65)
    print(f"{'Totals':<15} | {stats['up_to_date']:<15} | {stats['update_available']:<15} | {stats['failed_check']:<15}")
    
    sys.exit(0)


def cmd_set_library_root(args):
    """Handle set-library-root command."""
    print(f"Setting library root to: {args.path}")
    
    try:
        settings = SettingsManager(get_settings_path())
        settings.set_library_root(args.path)
        print("Library root saved successfully.")
        sys.exit(0)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to save library root: {e}")
        sys.exit(1)


def cmd_show_settings(args):
    """Handle show-settings command."""
    settings = SettingsManager(get_settings_path())
    all_settings = settings.get_all_settings()
    
    if not all_settings:
        print("No settings configured.")
        print("\nTo set a library root, run:")
        print("  python app.py set-library-root <path>")
        sys.exit(0)
    
    print("\nCurrent settings:")
    print("-" * 50)
    for key, value in all_settings.items():
        print(f"{key:<20} : {value}")
    print("-" * 50)
    
    sys.exit(0)


def cmd_update(args):
    """Handle update command."""
    try:
        download_root = resolve_download_root(getattr(args, 'download_root', None))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    print(f"Attempting to update mod with Workshop ID: {args.workshop_id}")
    print(f"Download root: {download_root}")

    result = update_mod(args.workshop_id, download_root, get_db_path())

    print(f"\nStatus: {result.get('status')}")
    print(f"Workshop ID: {result.get('workshop_id')}")
    if result.get('error'):
        print(f"Error: {result.get('error')}")

    if result.get('status') == 'success':
        print("\nUpdate completed successfully.")
        sys.exit(0)
    else:
        print("\nUpdate did not complete successfully.")
        sys.exit(1)


def cmd_update_all(args):
    """Handle update-all command."""
    try:
        download_root = resolve_download_root(getattr(args, 'download_root', None))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    print("Attempting to update all tracked mods")
    print(f"Download root: {download_root}")

    result = update_all_mods(download_root, get_db_path())

    print(f"\nUpdated: {result.get('updated')}")
    print(f"Skipped: {result.get('skipped')}")
    print(f"Failed: {result.get('failed')}")

    for detail in result.get('details', []):
        action = detail.get('action')
        workshop_id = detail.get('workshop_id')
        print(f" - {workshop_id}: {action}")

    if result.get('failed', 0) == 0:
        print("\nUpdate-all completed (no failures).")
        sys.exit(0)
    else:
        print("\nUpdate-all completed with some failures.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Stellaris Steam Workshop downloader and manager"
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Download command
    download_parser = subparsers.add_parser('download', help='Download a mod from Steam Workshop')
    download_parser.add_argument(
        "workshop_id",
        help="The Steam Workshop ID of the mod to download"
    )
    download_parser.add_argument(
        "--download-root",
        required=False,
        help="The root directory where the downloaded mod should be stored (optional if library root is configured)"
    )
    download_parser.set_defaults(func=cmd_download)
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all tracked mods')
    list_parser.set_defaults(func=cmd_list)
    
    # Check-updates command
    check_updates_parser = subparsers.add_parser('check-updates', help='Check for mod updates')
    check_updates_parser.set_defaults(func=cmd_check_updates)

    # Update command
    update_parser = subparsers.add_parser('update', help='Update a tracked mod if a newer version exists')
    update_parser.add_argument(
        'workshop_id',
        help='The Steam Workshop ID of the mod to update'
    )
    update_parser.add_argument(
        '--download-root',
        required=False,
        help='The root directory where the mod should be stored (optional if library root is configured)'
    )
    update_parser.set_defaults(func=cmd_update)

    # Update-all command
    update_all_parser = subparsers.add_parser('update-all', help='Update all tracked mods with available updates')
    update_all_parser.add_argument(
        '--download-root',
        required=False,
        help='The root directory where mods should be stored (optional if library root is configured)'
    )
    update_all_parser.set_defaults(func=cmd_update_all)
    
    # Set library root command
    set_library_root_parser = subparsers.add_parser('set-library-root', help='Set the default library root for mod storage')
    set_library_root_parser.add_argument(
        'path',
        help='The path to use as the default library root'
    )
    set_library_root_parser.set_defaults(func=cmd_set_library_root)
    
    # Show settings command
    show_settings_parser = subparsers.add_parser('show-settings', help='Display current application settings')
    show_settings_parser.set_defaults(func=cmd_show_settings)
    
    args = parser.parse_args()
    
    if not hasattr(args, 'func'):
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()