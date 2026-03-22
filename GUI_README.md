# Stellaris Mod Manager GUI

A desktop GUI for managing Stellaris Steam Workshop mods.

## Features

- **Two-pane layout**: Mod browser (right) and detail panel (left)
- **Search and sort**: Find mods quickly with search bar and multiple sort options
- **Mod details**: View comprehensive information about each mod
- **Workshop integration**: Browse and download mods directly
- **Update management**: Check for and install mod updates
- **Settings management**: Configure library root path

## Installation

1. Install Python dependencies:
   ```bash
   pip install PySide6 requests
   ```

2. Ensure SteamCMD is available (bundled with the application)

## Running the GUI

```bash
python gui.py
```

## GUI Layout

### Right Pane: Mod Browser
- **Search bar**: Filter mods by title or workshop ID
- **Sort dropdown**: Sort by:
  - Alphabetical
  - Last workshop update (newest first)
  - Last download time (newest first)
  - File size (largest first)
- **Mod list**: Shows downloaded mods with titles

### Left Pane: Mod Detail Panel
When a mod is selected, displays:
- **Preview image**: Shows mod preview (if available)
- **Title**: Mod name
- **Author**: Creator/steam ID
- **File size**: Size on disk in MB
- **Last workshop update**: When mod was last updated on Steam
- **Last download**: When you last downloaded the mod
- **Workshop URL**: Link to Steam Workshop page with "Open Workshop" button
- **File path**: Local path with "Open Folder" button
- **Description**: Full mod description

### Top Menu Bar

#### Workshop Menu
- **Browse Workshop**: Opens dialog to enter workshop ID and download
- **Check Updates**: Opens dialog to check for updates and select mods to update

#### Settings Menu
- **Settings**: Opens dialog to configure library root path

## Backend Integration

The GUI uses the existing core modules:
- `core/database.py`: SQLite database for mod metadata
- `core/steamcmd.py`: SteamCMD integration for downloads
- `core/workshop_api.py`: Steam Web API for metadata
- `core/updater.py`: Update checking and installation
- `core/settings.py`: Persistent settings storage

## Metadata Fields

The GUI displays the following metadata (fetched from Steam API):
- ✅ **title**: Mod name
- ✅ **description**: Full description text
- ✅ **preview_url**: Preview image URL (displayed as placeholder)
- ✅ **creator**: Author/creator Steam ID
- ✅ **remote_updated_at**: Last workshop update timestamp
- ✅ **time_created**: Creation timestamp
- ✅ **file_size**: File size in bytes
- ✅ **content_path**: Local file path
- ✅ **last_downloaded_at**: Download timestamp

## Testing

1. **Launch GUI**:
   ```bash
   python gui.py
   ```

2. **Set library root** (if not set):
   - Go to Settings → Settings
   - Choose or type a directory path
   - Click Save

3. **Download a mod**:
   - Go to Workshop → Browse Workshop
   - Enter a workshop ID (e.g., 1595876588)
   - Click OK

4. **View mod details**:
   - Click on a mod in the right pane
   - View details in the left pane

5. **Test search/sort**:
   - Use search bar to filter mods
   - Try different sort options

6. **Test updates**:
   - Go to Workshop → Check Updates
   - Select mods and update

## Current Limitations

- Preview images are not downloaded/displayed (shows placeholder)
- No embedded Steam Workshop browser
- No collection support
- No launcher integration
- No GUI for initial setup

## File Structure

```
StellarisDownloaderV1/
├── gui.py                 # Main GUI application
├── app.py                 # CLI application
├── core/
│   ├── database.py        # SQLite database management
│   ├── steamcmd.py        # SteamCMD integration
│   ├── workshop_api.py    # Steam Web API client
│   ├── updater.py         # Update checking logic
│   └── settings.py        # Settings persistence
├── data/                  # Application data directory
│   ├── app.db            # SQLite database
│   └── settings.json     # Application settings
└── steamcmd/             # Bundled SteamCMD
```</content>
<parameter name="filePath">c:\StellarisDownloaderV1\GUI_README.md