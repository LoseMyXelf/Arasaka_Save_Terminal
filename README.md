# Arasaka Save Terminal

A standalone Windows desktop tool for Cyberpunk 2077 save slots. It is built with Python's standard library and does not require CET or a runtime mod.

## Run the downloaded portable version

Extract the entire release ZIP, then open `ArasakaSaveTerminal.exe`. Keep the `_internal` folder beside the EXE. Python is included in the package.

## Run from source

1. Install Python 3.10 or newer for Windows, including pip and Tcl/Tk (Tkinter). The release build was made with Python 3.15, 64-bit.
2. Download or clone this repository and extract it into a folder.
3. Double-click `launch_editor.bat`.

The launcher uses the project's `.venv` when present, then tries the Windows `py` launcher and `python`. Source execution requires only the Python standard library.

You can also run it from a terminal in the project folder:

```powershell
py -3 .\CyberpunkSaveEditor.py
```

The app scans `%USERPROFILE%\Saved Games\CD Projekt Red\Cyberpunk 2077` by default. Choose another folder from the sidebar when needed.

## Build your own portable Windows application

Install 64-bit Python with pip and Tkinter, then double-click `build_exe.bat`. An internet connection is required to download build dependencies. The script creates a local `.venv`, installs the pinned PyInstaller version from `requirements-build.txt`, and builds using `ArasakaPortable.spec` with UPX disabled.

The result is `dist\portable\ArasakaSaveTerminal\ArasakaSaveTerminal.exe`. Distribute the **entire ArasakaSaveTerminal folder**, including `_internal`. Extract all files before launching.

Equivalent manual commands from the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --distpath dist\portable --workpath build\portable ArasakaPortable.spec
```

Keep `CyberpunkSaveEditor.py`, `save_format.py`, `ArasakaPortable.spec`, and the `assets` folder together when building. `NightCitySaveStudio.spec` is the older single-file packaging option. The batch script uses the portable-folder specification.

## Repository files

Upload the source files, both batch scripts, build specifications, `requirements-build.txt`, README, and `assets` folder. Exclude generated `.venv`, `build`, `dist`, and `__pycache__` folders from source commits.

## Current capabilities

- Reads save-slot metadata and validates the `sav.dat` header.
- Decodes save chunks and reads wallet, character level, Street Cred, five attributes, and supported proficiency levels from `sav.dat`.
- Exports changed wallet/character Int32 values to a separate complete save folder. The source slot is never overwritten; exports include the source `sav.dat` as `sav.dat.ncsbak`.
- Creates a full copy of a selected save folder.
- Creates a persistent `sav.dat.ncsbak` beside a selected save.
- Restores `sav.dat` from that backup after confirmation. Before restoring, it preserves the current file as `sav.dat.before-restore-<timestamp>`.
- Does not depend on CET and has no item injection workflow.

## Editing status

Wallet, level, Street Cred, attributes, supported skill ranks, and the two point pools can be edited in exported copies. Skill-rank editing changes the saved rank only; related XP is not changed. Attribute values are limited to 0–20, level and skill ranks to 0–60, Street Cred to 0–50, Attribute Points and Perk Points to 0–120, and wallet to 0–99,999,999. Character and wallet edits have been tested in-game on selected saves. Compatibility with every save or game version is not guaranteed.

Close the game before editing. The export workflow writes a separate full save folder and includes the selected original `sav.dat` as a restore backup. Game acceptance of edited saves should be confirmed by loading an exported copy before relying on it.
