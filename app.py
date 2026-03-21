import argparse
import sys
from core.steamcmd import download_mod

def main():
    parser = argparse.ArgumentParser(
        description="Download Stellaris mods from Steam Workshop using SteamCMD"
    )
    parser.add_argument(
        "workshop_id",
        help="The Steam Workshop ID of the mod to download"
    )
    parser.add_argument(
        "--download-root",
        required=True,
        help="The root directory where the downloaded mod should be exported"
    )
    
    args = parser.parse_args()
    
    print(f"Attempting to download mod with Workshop ID: {args.workshop_id}")
    print(f"Download root: {args.download_root}")
    
    result = download_mod(args.workshop_id, args.download_root)
    
    print(f"Status: {result['status']}")
    print(f"Mod library path: {result['final_path'] or 'N/A'}")
    print(f"Junction created: {result['junction_created']}")
    print(f"Junction verified: {result['junction_verified']}")
    print(f"Junction path: {result['junction_path']}")
    print(f"Library target path: {result['library_target_path']}")
    
    if result["status"] == "success":
        print("Download completed successfully.")
        sys.exit(0)
    else:
        print("Download failed.")
        if result["error"]:
            print(f"Error: {result['error']}")
        sys.exit(1)

if __name__ == "__main__":
    main()