# R6 Match Stats in the browser

The browser edition is R6 Match Stats as one HTML page: open it in any browser, drop a match's
`.rec` files, its folder or a `.zip`, and get the same Pro League-style scoreboards and
round-by-round breakdown as the Windows app. It also has the League & Team picker (Liquipedia's
S- to D-Tier events, NECC's divisions, or a league of your own) a separate twelve-match demo season, saved Match History, Ask, Team Analytics, Operator Analytics, Season Tracker, and Team Theme.

Nothing is uploaded anywhere. The replay is read inside the browser tab by
[r6-dissect](../dissect) compiled to WebAssembly (`wasm/main.go`), and the stats come from the
app's own `scripts/parser.py` and `scripts/metrics_engine.py` running in
[Pyodide](https://pyodide.org) (Python for the browser) through `bridge.py`, so the numbers
match the Windows app exactly.

Each release attaches it as **R6MatchStats-Browser.html** (built by `desktop/build.ps1`, with
the Windows app, and listed in the release's SHA256SUMS.txt), and the app's **Get the Windows
app** page links to it.

## Build it

With Go 1.23 or newer and the app's Python environment:

```bash
.venv\Scripts\python browser\build.py
```

That writes `browser/dist/`:

- `R6MatchStats.html`: the whole app in one file (about 13 MB) to double-click or send to
  someone. The parser, the stats code, the event lists and the demo match are inside it; only
  Python itself loads from jsDelivr, once, so the first replay needs an internet connection.
- `share/`: the same page for hosts that only serve a page's own files. Pass
  `--pyodide <folder>` with Pyodide 0.27.7's core files (`pyodide.js`, `pyodide.asm.js`,
  `pyodide.asm.wasm`, `python_stdlib.zip`, `pyodide-lock.json`, and the SQLite wheel listed in `build.py`, from
  `https://cdn.jsdelivr.net/pyodide/v0.27.7/full/`) to put them next to it.

The build compiles the parser the first time (`browser/r6-dissect.wasm`); delete that file to
compile it again after changing `dissect/`. Rebuild after updating the Liquipedia snapshot or
NECC's divisions, since the page carries a copy of both.

## Limits

- Python takes a few seconds to start the first time a replay is opened, then stays loaded.
- A browser tab holds a few hundred MB, plenty for one match; open one match folder at a time.
- Zip files need JSZip from cdnjs, so they need an internet connection the first time.
- Confirmed matches and selected players are saved in IndexedDB on this device. Export/import a workspace JSON backup to move them between browsers. Demo matches never enter your saved workspace.
- Native window docking and automatic replay-folder discovery remain desktop integrations; browser uploads provide the same analysis.
- Seasons can include the same confirmed match independently without double counting.
