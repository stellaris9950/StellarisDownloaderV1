# Windows Packaging

## Build requirements

- Windows
- Python installed
- Project dependencies installed
- `pyinstaller` installed
- `steamcmd/` populated in the repo before building

## Build command

```powershell
pip install pyinstaller
.\build_windows.ps1
```

Or directly:

```powershell
python -m PyInstaller --noconfirm --clean .\StellarisModManager.spec
```

## Output

The packaged app is created in:

```text
dist\StellarisModManager\
```

Run it with:

```powershell
.\dist\StellarisModManager\StellarisModManager.exe
```

## Runtime paths

Writable runtime files are not stored inside the bundled app folder.

The app uses:

```text
%LOCALAPPDATA%\StellarisModManager\
```

Within that folder it creates:

- `data\app.db`
- `data\settings.json`
- `logs\app.log`
- `steamcmd\...` runtime SteamCMD copy

This means the packaged build can run from a read-only location such as:

- `C:\Program Files\StellarisModManager`
- a copied `dist\StellarisModManager\` folder
- a shared network folder with user-local writable profile storage

## SteamCMD bundling

The PyInstaller build bundles only:

- `steamcmd.exe`

This matches the distribution constraint that SteamCMD should handle its own setup and updates.

At runtime:

- source-tree execution uses the project `steamcmd` folder directly
- packaged execution copies only `steamcmd.exe` into `%LOCALAPPDATA%\StellarisModManager\steamcmd`
- the app creates writable runtime folders there for SteamCMD caches and workshop content
- SteamCMD itself is responsible for populating any additional files it needs on first run

This keeps SteamCMD writable on machines where the packaged app folder is read-only.

## Qt / WebEngine packaging

The spec file uses PyInstaller collection helpers for `PySide6`, including:

- `PySide6.QtWebEngineCore`
- `PySide6.QtWebEngineWidgets`
- `PySide6.QtWebChannel`

That ensures the embedded Workshop browser and Qt WebEngine runtime are included in the one-folder build.

## Test process

1. Build with PyInstaller.
2. Copy `dist\StellarisModManager` to a test machine without Python.
3. Run `StellarisModManager.exe`.
4. Confirm the app launches.
5. Open Settings and verify `settings.json` is created under `%LOCALAPPDATA%\StellarisModManager\data`.
6. Trigger a download and confirm:
   - SteamCMD starts successfully
   - `%LOCALAPPDATA%\StellarisModManager\steamcmd` is created
   - database and settings remain writable
7. Test Workshop browser, queue downloads, and update flows.
8. Confirm the embedded Workshop browser opens correctly. If it does not, inspect the packaged folder for Qt WebEngine files and `QtWebEngineProcess`.
