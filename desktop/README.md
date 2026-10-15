# Windows app

R6 Match Stats for Windows is the Streamlit dashboard (`scripts/app.py`)
packaged with Python, its libraries and `r6-dissect.exe`, so it runs on a PC
without installing anything else. It opens in its own window (Microsoft Edge
WebView2, built into Windows 10 and 11) with the app's icon in the taskbar,
runs the dashboard's server hidden in the background (only reachable from
that PC), and finds the game's MatchReplay folder on its own.

Users get it from the website's **Get the Windows app** page:

- `R6MatchStats-Setup.exe`, the installer: installs for the current user (no
  administrator needed), adds Start menu and desktop shortcuts, and an
  uninstaller in **Settings > Apps**. Running a newer installer updates the
  app in place.
- `R6MatchStats-Windows.zip`, the portable version: extract it and run
  `R6MatchStats\R6MatchStats.exe`.

## Build

From the repo root, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build.ps1
```

This needs Python 3.12+ (it uses `.venv` if there is one), Go 1.23+ and Inno
Setup 6, which the script installs with winget if it's missing. The build
always compiles the bundled parser from the current source so it cannot
silently package an outdated executable. It produces:

- `dist\R6MatchStats\R6MatchStats.exe` (run it to try the build)
- `dist\R6MatchStats-Setup.exe` (the installer)
- `dist\R6MatchStats-Windows.zip` (the portable version)
- `dist\SHA256SUMS.txt` (the SHA-256 of both, for people to check their download)

GitHub builds both for every published release with the **Windows app**
workflow (`.github/workflows/windows-app.yaml`), which also installs the app,
opens its window, checks that the dashboard shows up in it, and uninstalls it.
Set `R6_VERSION` (the workflow passes the release tag) to set the app's
version; otherwise it's `APP_VERSION` in `scripts/app_info.py`.

## Files

- `launcher.py` is the entry point. It opens the app window, starts the
  dashboard's server as a hidden copy of itself (`R6MatchStats.exe --serve`),
  and stops the server when the window closes. While the server starts, the
  window shows a launch screen: the app's icon draws itself in the saved school
  theme's color, and a three-step bar follows the real startup (checking the
  app's files, starting the stats engine, loading the dashboard). It stays up at
  least 3 seconds so the animation plays out, then fades into the dashboard. It
  respects Windows' "reduce animations" setting. Opening the app while it's
  already open brings the existing window to the front. Without WebView2 it
  falls back to the default browser. The app has no console, so Windows would
  give every console program it starts (the replay parser, or Git when
  Streamlit looks up the app's folder) a terminal window of its own; the
  launcher makes all of them run without one (`hide_console_windows`, tested
  in `test_launcher.py`).
- `windows_shell.py` makes the app behave like other Windows apps, using ctypes only:
  - **Its own identity** (AppUserModelID `R6MatchStats.Desktop`, also set on the
    installer's shortcuts). A pinned taskbar or Start icon groups with the open
    window.
  - **A jump list**: right-click the app on the taskbar or Start for **Dock to
    the right**, **Dock to the left** and **Full window**. These run
    `R6MatchStats.exe --dock right|left|off`, which the open window carries out.
  - **Docking** (`AppBar`): the window becomes a borderless, always-on-top panel,
    420 pixels wide (scaled with the display), on the left or right edge of its
    screen. Windows reserves that strip the way it reserves the taskbar's, so
    maximized windows fit beside it. While docked, the window shows
    `scripts/dock.py`: your latest match round by round, your numbers and your
    recent form, from the matches you've added. It refreshes every 15 seconds,
    and says when the game has saved a match you haven't added yet.

  You can dock from the Dashboard's sidebar, from the jump list, or from the
  panel itself (**Dock left/right** to move it, **Full window** to undock). The
  pages run in the server process, so they ask the window with small command
  files (`app_info.send_window_command`, in
  `%LOCALAPPDATA%\R6MatchStats\window-commands`). The app reopens the way it
  was closed, docked or not (`window.json`).
- `R6MatchStats.spec` tells PyInstaller what to bundle: a windowed exe (no
  console) with the icon and version details. The app files ship as plain
  files, because Streamlit runs `app.py` and its pages from disk.
- `integrity.py` (`AppIntegrity`) lists every file of the build with its
  SHA-256 (`_internal\manifest.json`). The launcher checks the app's folder
  against that list every time it opens, and if a file was changed, added or
  removed, it refuses to start and says which. `test_integrity.py` tests it.
- `installer.iss` is the Inno Setup script for the installer. Before
  installing, it shows the notice that the app is unofficial and what it does
  and doesn't do.
- `build.ps1` builds `r6-dissect.exe`, runs PyInstaller, signs the programs
  when it has a certificate (see **Code signing**), zips the result and
  builds the installer. It also stamps `build\repo.txt` (the GitHub repo the
  app checks for updates), `build\version.txt` and `build\notice.txt`, writes
  the file list for the integrity check, and writes `dist\SHA256SUMS.txt`.
- `assets\app.ico` is the icon (with `scripts\icon.png`, the page's favicon),
  drawn by `make_icon.py`.

## Code signing

When the build has a code-signing certificate, it signs everything it produces:
- `R6MatchStats.exe` and `r6-dissect.exe`, right after PyInstaller. Signing changes a file, so this has to happen before the app's file list records their SHA-256s.
- The installer and its uninstaller, through Inno Setup.

`sign.ps1` signs with the Windows SDK's `signtool` and timestamps every signature, so signatures stay valid after the certificate expires.

To sign releases, add two repository secrets in **Settings > Secrets and variables > Actions**:

- `WINDOWS_SIGNING_CERT`: the certificate's `.pfx` file, base64-encoded. In PowerShell,
  `[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\cert.pfx")) | Set-Clipboard`
  copies it, ready to paste.
- `WINDOWS_SIGNING_PASSWORD`: the `.pfx` file's password.

From then on, the **Windows app** workflow signs each release and checks every signature: signed with this certificate, timestamped, and valid. It also attaches `SIGNATURE.txt`, which names the signer and gives the certificate's thumbprint. The download page shows both so people can check their download.

Without the secrets:
- Releases are published unsigned, and the workflow warns about it.
- Every other build signs with a throwaway certificate, so the signing steps are still tested on each build.

To sign a build on your own PC, set `R6_SIGN_PFX` to the `.pfx` file and `R6_SIGN_PFX_PASSWORD` to its password before running `build.ps1`.

Only a certificate from a certificate authority that Windows trusts replaces "Unknown publisher" with your name. A self-signed certificate doesn't. Even with a trusted certificate, SmartScreen may keep warning while the certificate is new, until enough people have downloaded apps signed with it.

Since June 2023, certificate authorities have issued new code-signing certificates only on hardware keys, and those can't be exported as a `.pfx`. A `.pfx` fits a certificate issued before then. A newer certificate needs its provider's cloud signing service in place of `sign.ps1`'s `/f` option.

The app writes its log to `%LOCALAPPDATA%\R6MatchStats\app.log`, and its stats
database (the matches you add, the teams you build and tracked season stats)
to `%LOCALAPPDATA%\R6MatchStats\season_stats.db`.

For testing, set `R6_NO_WINDOW=1` to open the app in the default browser
instead of a window, or `R6_SMOKE_TEST=result.json` to load the app, record
what the window shows, and quit. On Windows the smoke test also docks the
window to the right, checks that it took its strip of the screen and shows the
docked page, then undocks it and checks the strip was given back. It also
checks the app's identity and jump list. The exit code is 0 if all of that
worked. The screenshots are saved as `result.json.png` and
`result.json.docked.png`.
