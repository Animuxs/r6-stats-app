# Unity Windows client

`R6MatchStats/` is the Unity 6 desktop-client project. The command-center scene
is generated at runtime: it includes the tactical 3D board, module navigation,
replay-path input, a player picker for saving a parsed match to a season (only
the players you pick are saved), school/roster selection, and UnityWebRequest
calls to the same FastAPI service as the React client.

## Run

Open `R6MatchStats/` in Unity 6000.0.34f1, resolve packages, and press Play. The
client expects the local API at `http://127.0.0.1:8000/api/v1` by default. Start
it from the repository root in a separate terminal:

```powershell
python -m pip install -r scripts
equirements-api.txt
$env:R6_DESKTOP = "1"
python -m uvicorn api:app --app-dir scripts --host 127.0.0.1 --port 8000
```

The API and Unity client must use the same database. On Windows, `R6_DESKTOP=1`
stores it under `%LOCALAPPDATA%\R6MatchStats\season_stats.db`.

## Build

Install a licensed Unity Editor matching the project version, then run from the
repository root:

```powershell
powershell -ExecutionPolicy Bypass -File unity\build-windows.ps1
```

Set `UNITY_EDITOR_PATH` if the Editor is installed in a nonstandard location.
The executable is written to `unity/R6MatchStats/Builds/Windows/`.

## Data and security

The Unity client uses the shared REST routes for replay parsing, roster tracking,
match logging, season summaries, match history, and NECC catalogs. Replay paths
are read by the local API and still pass through `ReplayScanner`. The API binds
to loopback by default and has no authentication; keep it local unless an
authenticated deployment is added. NECC auto-sync requires an authorized JSON
feed configured with `NECC_R6_DATA_URL`, or a catalog imported by the React
client.

This Codespace has no Unity Editor or license, so the project and Windows build
script are not editor-compiled or packaged here. The existing Python Windows
package remains available until a Unity build can be verified on a licensed
Windows machine.