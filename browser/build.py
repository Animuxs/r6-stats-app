"""
Build the browser edition of R6 Match Stats from browser/index.html:

    python browser/build.py

(It compiles the replay parser to WebAssembly first, with Go 1.23 or newer on PATH, when
browser/r6-dissect.wasm isn't there; delete that file to rebuild it after changing dissect/.)

It writes two versions to browser/dist/:

- R6MatchStats.html: one file to open in any browser by double-clicking it, nothing to install.
  Everything is inside it (the replay parser, the app's stats code, the Liquipedia and NECC event
  lists, the demo match) except Python itself (Pyodide), which it loads from jsDelivr the first time.
- share/: the same page for hosts that only allow a page's own files (no fetching from other sites),
  with the parser and Pyodide as files next to it. Point --pyodide at a folder holding Pyodide's
  core files (pyodide.js, pyodide.asm.js, pyodide.asm.wasm, python_stdlib.zip, pyodide-lock.json)
  to copy them in; the standard library zip goes inside the page, for hosts that refuse .zip files.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "scripts"
sys.path[:0] = [str(SCRIPTS), str(HERE)]

import events  # noqa: E402
from presentation import TOKENS_CSS
from app_info import APP_VERSION, RELEASES_URL  # noqa: E402

PYODIDE_VERSION = "0.27.7"
PYODIDE_CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
PYODIDE_FILES = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "pyodide-lock.json", "sqlite3-1.0.0-cp312-cp312-pyodide_2024_0_wasm32.whl")  # + python_stdlib.zip, inlined
# the app's own Python the page runs: the parser's normalizer and the stats engine, unchanged
PYTHON = {name: SCRIPTS / name for name in ("parser.py", "file_guard.py", "metrics_engine.py", "sample_data.py",
                                           "stats_db.py", "operator_stats.py", "team_tables.py", "season_stats.py", "ask_engine.py", "demo_season.py")}
PYTHON["bridge.py"] = HERE / "bridge.py"
LIGHT_EVENT_KEYS = ("id", "name", "source", "tier", "kind", "organizer", "location", "region", "mode", "start", "end",
                    "prize_usd", "team_count", "teams", "url", "website")


def config(share: bool, wasm: bytes, stdlib: bytes = b"", version: str = APP_VERSION) -> dict:
    import bridge
    import demo_season

    catalog = events.load_catalog(events.BUNDLED) or {"events": [], "captured_at": ""}
    cutoff = date.today()
    return {
        "version": version,
        "share": share,
        "releasesUrl": RELEASES_URL,
        "pyodideBase": "pyodide/" if share else PYODIDE_CDN,
        "pyodideScript": "pyodide/pyodide.js" if share else PYODIDE_CDN + "pyodide.js",
        "pyodideScriptFallback": PYODIDE_CDN + "pyodide.js",
        "wasmUrl": "r6-dissect.wasm" if share else "",
        "wasmBase64": "" if share else base64.b64encode(wasm).decode("ascii"),
        # hosts that refuse .zip files get Pyodide's standard library inside the page
        "stdlibBase64": base64.b64encode(stdlib).decode("ascii") if share and stdlib else "",
        "python": {name: path.read_text(encoding="utf-8") for name, path in PYTHON.items()},
        "events": [{k: e.get(k) for k in LIGHT_EVENT_KEYS} for e in catalog["events"] if events.in_window(e, cutoff)],
        "eventsCapturedAt": catalog.get("captured_at", ""),
        "attribution": events.ATTRIBUTION,
        "necc": events.necc_events(),
        "neccCapturedAt": events.necc_captured_at(),
        "demo": json.loads(bridge.demo()),
        "demoSeason": [{"key": key, "data": match, "players": demo_season.ROSTER, "season": ""}
                       for key, match in demo_season.season().items()],
        "demoTeam": {demo_season.TEAM: demo_season.ROSTER},
        "rosters": json.loads((SCRIPTS / "necc_rosters.json").read_text(encoding="utf-8"))["teams"],
    }


def page(share: bool, wasm: bytes, stdlib: bytes = b"", version: str = APP_VERSION) -> str:
    template = (HERE / "index.html").read_text(encoding="utf-8")
    goroot_exec = (HERE / "wasm_exec.js").read_text(encoding="utf-8")
    # "</" can't appear inside an inline script, or the HTML parser would end it there
    settings = json.dumps(config(share, wasm, stdlib, version), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (template.replace("/*@@CONFIG@@*/", f"window.R6 = {settings};")
            .replace("/*@@WASM_EXEC@@*/", goroot_exec.replace("</", "<\\/"))
            .replace("/*@@THEME@@*/", TOKENS_CSS)
            .replace("/*@@WORKSPACE@@*/", (HERE / "workspace.js").read_text(encoding="utf-8").replace("</", "<\\/")))
    if share:  # the host wraps the page in its own document
        return html
    head, body = html.split("</style>", 1)
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f"{head}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def ensure_parser() -> None:
    """browser/r6-dissect.wasm and the wasm_exec.js of the Go that built it."""
    wasm = HERE / "r6-dissect.wasm"
    if not wasm.is_file():
        env = dict(os.environ, GOOS="js", GOARCH="wasm")
        subprocess.run(["go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(wasm), "./wasm"], cwd=ROOT, env=env,
                       check=True)
    if not (HERE / "wasm_exec.js").is_file():
        goroot = Path(subprocess.run(["go", "env", "GOROOT"], capture_output=True, text=True, check=True).stdout.strip())
        found = next(p for p in (goroot / "lib" / "wasm" / "wasm_exec.js", goroot / "misc" / "wasm" / "wasm_exec.js")
                     if p.is_file())
        shutil.copyfile(found, HERE / "wasm_exec.js")


def main() -> None:
    options = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    options.add_argument("--pyodide", type=Path, help="folder with Pyodide's core files, for dist/share")
    options.add_argument("--out", type=Path, default=HERE / "dist")
    options.add_argument("--version", default=APP_VERSION, help="the version the page shows (default: app_info's)")
    args = options.parse_args()
    ensure_parser()
    wasm = (HERE / "r6-dissect.wasm").read_bytes()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    single = out / "R6MatchStats.html"
    single.write_text(page(False, wasm, version=args.version), encoding="utf-8")
    print(f"{single}  ({single.stat().st_size / 1e6:.1f} MB)")

    share = out / "share"
    (share / "pyodide").mkdir(parents=True, exist_ok=True)
    stdlib = (args.pyodide / "python_stdlib.zip").read_bytes() if args.pyodide else b""
    (share / "index.html").write_text(page(True, wasm, stdlib, args.version), encoding="utf-8")
    shutil.copyfile(HERE / "r6-dissect.wasm", share / "r6-dissect.wasm")
    if args.pyodide:
        for name in PYODIDE_FILES:
            shutil.copyfile(args.pyodide / name, share / "pyodide" / name)
    print(f"{share}  (index.html {(share / 'index.html').stat().st_size / 1e6:.1f} MB"
          + ("" if args.pyodide else "; no --pyodide, so pyodide/ is empty") + ")")


if __name__ == "__main__":
    main()
