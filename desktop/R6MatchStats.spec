# PyInstaller spec for the Windows app: dist/R6MatchStats/R6MatchStats.exe.
# Build with desktop/build.ps1, which builds r6-dissect.exe and stamps build/repo.txt first.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
ICON = ROOT / "desktop" / "assets" / "app.ico"
STAMPED_VERSION = ROOT / "build" / "version.txt"  # written by build.ps1
APP_VERSION = (STAMPED_VERSION.read_text().strip() if STAMPED_VERSION.is_file()
               else re.search(r'or "([\d.]+)"', (ROOT / "scripts" / "app_info.py").read_text()).group(1))

# The app ships as plain files, because Streamlit runs app.py (and its pages) from disk.
APP_FILES = ["app.py", "report.py", "dock.py", "ask.py", "history.py", "operators.py", "teams.py", "league.py",
             "events.py", "liquipedia_events.json", "necc_divisions.json", "rosters.py", "necc_rosters.json", "team_ui.py",
             "download.py", "app_info.py", "ui.py", "presentation.py", "operator_stats.py", "team_tables.py", "branding.py", "appearance.py", "necc_data.py",
             "necc_season.json", "season_stats.py", "sources.py", "stats_db.py", "ask_engine.py",
             "parser.py", "file_guard.py", "metrics_engine.py", "sample_data.py", "demo_season.py", "session.py", "tab.py", "matchday.py", "matchday_data.py", "matchday_hub.py",
             "desktop_settings.py", "team_accounts.py", "team_runtime.py", "team_hub.py", "map_pool.py", "scoreboard_image.py",
             "assistant.py", "assistant_knowledge.py", "assistant_ui.py", "question_reports.py",
             "charts.py", "chart_engine.py", "chart_view.py", "icon.png"]
datas = [(str(ROOT / "scripts" / name), "scripts") for name in APP_FILES]
for stamp in ("repo.txt", "version.txt"):
    if (ROOT / "build" / stamp).is_file():
        datas.append((str(ROOT / "build" / stamp), "scripts"))
datas += collect_data_files("streamlit") + copy_metadata("streamlit")

hiddenimports = (
    collect_submodules("streamlit")
    # uvicorn and websockets load their protocol modules by name at runtime
    + collect_submodules("uvicorn")
    + collect_submodules("websockets")
    # the app window (pywebview ships its own PyInstaller hook for its data files)
    + ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr",
       "PIL.ImageGrab",  # the launcher's smoke test screenshot
       "PIL.ImageDraw", "PIL.ImageFont"]  # the Dashboard's scoreboard image (scripts/scoreboard_image.py)
    # standard library modules the app files use, which PyInstaller can't see in plain files
    # (scripts/test_app.py checks this list against their imports)
    + ["abc", "ast", "base64", "calendar", "collections", "collections.abc", "concurrent.futures", "contextlib", "csv", "dataclasses", "datetime",
       "difflib", "functools", "gzip", "html", "http.client", "io", "ipaddress", "itertools", "json", "logging", "operator", "os", "pathlib",
       "random", "re", "shutil", "sqlite3", "subprocess", "sys", "tempfile", "threading", "time", "typing", "unicodedata",
       "urllib.parse", "urllib.request", "uuid", "zipfile", "hashlib", "hmac", "secrets"]
)

# the Details tab of the exe's Properties, and the name Task Manager shows (Windows only)
version = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    numbers = tuple(int(n) for n in (APP_VERSION.split(".") + ["0", "0", "0"])[:4])
    version = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("ProductName", "R6 Match Stats"),
                StringStruct("FileDescription", "R6 Match Stats"),
                StringStruct("FileVersion", APP_VERSION),
                StringStruct("ProductVersion", APP_VERSION),
                StringStruct("InternalName", "R6MatchStats"),
                StringStruct("OriginalFilename", "R6MatchStats.exe"),
                StringStruct("LegalCopyright", "MIT License"),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT / "scripts")],
    binaries=[(str(ROOT / "r6-dissect.exe"), ".")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="R6MatchStats",
          console=False,  # a normal app window, no black console window
          icon=str(ICON), version=version)
coll = COLLECT(exe, a.binaries, a.datas, name="R6MatchStats")
