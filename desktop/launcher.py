"""
launcher.py
===========
Entry point of the Windows app (R6MatchStats.exe, built by build.ps1 with
PyInstaller). It opens the app in its own window (Microsoft Edge WebView2,
built into Windows 10 and 11) and runs the dashboard's server as a hidden
child process on this PC only, stopping it when the window closes.

    R6MatchStats.exe                      the app window
    R6MatchStats.exe --dock right|left    the app window, docked to that edge of the screen
    R6MatchStats.exe --dock off           the app window, undocked (the jump list's "Full window")
    R6MatchStats.exe --serve PORT PID     the server (started by the window)

With the app already open, the --dock ones dock or undock its window instead.

Settings, for testing:
    R6_NO_WINDOW=1         open the app in the default browser instead of a window
    R6_SMOKE_TEST=FILE     load the app, write what the window shows to FILE
                           (JSON), then quit: exit code 0 if the app rendered.
                           Also saves a screenshot of the screen as FILE.png
From source:  python desktop/launcher.py   (needs pip install pywebview)
"""

from __future__ import annotations

import functools
import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from integrity import AppIntegrity

# Never write compiled .pyc caches into the app's folder: the integrity check
# would (rightly) flag them as files that weren't there when it was built.
sys.dont_write_bytecode = True

# the unpacked app, or the repo root when run from source
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from app_info import (  # noqa: E402  ships as a plain file next to app.py
    APP_NAME, APP_VERSION, WINDOW_COMMANDS, desktop_data_dir, quiet_windows_connection_resets, send_window_command,
)

IS_WINDOWS = sys.platform == "win32"
STARTUP_TIMEOUT = 90  # seconds; the first start after install is slow while antivirus scans the files
DATA_DIR = desktop_data_dir()
LOG_FILE = DATA_DIR / "app.log"
MIN_LAUNCH_SECONDS = 3.0  # the launch screen stays at least this long, so its animation plays out
FADE_SECONDS = 0.6        # the launch screen's fade into the app (SPLASH's transition)

# The app's identity on Windows (its AppUserModelID): the taskbar groups the window, its pinned
# taskbar and Start icons and its jump list under it. installer.iss gives its shortcuts the same.
APP_ID = "R6MatchStats.Desktop"
LEFT, RIGHT = "left", "right"
DOCK_WIDTH = 420  # the docked panel's width, in logical pixels (scaled with the display)
DOCK_STATE = "window.json"  # {"dock": "left" | "right" | null}: docked when the app last closed
JUMP_LIST_TASKS = [  # (title, arguments, description): right-click the app on the taskbar or Start
    ("Dock to the right", "--dock right", "Dock R6 Match Stats to the right edge of the screen"),
    ("Dock to the left", "--dock left", "Dock R6 Match Stats to the left edge of the screen"),
    ("Full window", "--dock off", "Open R6 Match Stats as a full window"),
]


def log(message: str) -> None:
    """Add a line to the app's log (the server writes its output there too)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as out:
            out.write(message.rstrip() + "\n")
    except OSError:
        pass


# ----------------------------------------------------------- no terminals --
def hide_console_windows() -> None:
    """This app has no console, so Windows gives each console program it starts its own
    console window: an empty terminal flashing up for every replay parse (r6-dissect),
    or when Streamlit asks Git about the app's folder. Make every program started from
    this process run without one, whichever code starts it (a program that explicitly
    asks for a new console still gets it). Windows only; safe to call more than once."""
    if not IS_WINDOWS or getattr(subprocess.Popen.__init__, "hides_console_windows", False):
        return
    original = subprocess.Popen.__init__
    explicit = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS

    @functools.wraps(original)
    def init(self, *args, **kwargs):
        # creationflags is Popen's 14th parameter; nobody passes it by position, but leave that alone
        if len(args) < 14 and not kwargs.get("creationflags", 0) & explicit:
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    init.hides_console_windows = True
    subprocess.Popen.__init__ = init


# ------------------------------------------------------------------ server --
def serve(port: int, parent_pid: int) -> int:
    """Run the dashboard's server on 127.0.0.1:port until the window's process exits."""
    parser_exe = ROOT / "r6-dissect.exe"
    if parser_exe.is_file():
        os.environ["R6_DISSECT_BIN"] = str(parser_exe)
    os.environ["R6_DESKTOP"] = "1"
    if sys.stdout is None:  # a windowed build can drop the log file handle start_server passed in
        sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", errors="replace", buffering=1)
    quiet_windows_connection_resets()
    threading.Thread(target=_exit_with_parent, args=(parent_pid,), daemon=True).start()

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(SCRIPTS / "app.py"),
        "--global.developmentMode=false",  # a PyInstaller build otherwise looks like a Streamlit dev checkout
        "--server.address=127.0.0.1",  # only this PC can reach the app
        f"--server.port={port}",
        "--server.headless=true",  # the window shows the app, not a browser tab
        "--server.fileWatcherType=none",
        "--server.maxUploadSize=2048",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",  # no Streamlit developer menu (Deploy, Rerun, ...)
        "--theme.base=dark",
        "--theme.primaryColor=#52d5f2",  # the default school theme's color on sliders, toggles, options
    ]
    return stcli.main()


def _exit_with_parent(pid: int) -> None:
    """Stop the server when the window's process is gone, even if it crashed."""
    if IS_WINDOWS:
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return  # can't watch it; the window still stops this server when it quits
        ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    else:
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(1)
    os._exit(0)


def start_server() -> tuple[subprocess.Popen, str]:
    """Start the server as a hidden child process; returns it and the app's URL."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_FILE, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        me + ["--serve", str(port), str(os.getpid())],
        stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
    )
    return proc, f"http://127.0.0.1:{port}/"


def integrity_problems() -> list[str]:
    """What's wrong with the installed app's files (see integrity.py); nothing when run from source."""
    if not getattr(sys, "frozen", False):
        return []
    return AppIntegrity(Path(sys.executable).parent).verify()


class Server:
    """The dashboard's server: started only once the app's files check out, and
    stopped when the app quits."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self.problems: list[str] = []

    def start(self) -> bool:
        """Check the app's files, then start the server. False if the check failed."""
        if self.proc is None and not self.problems:
            self.problems = integrity_problems()
            if self.problems:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                LOG_FILE.write_text("The app's files failed their check:\n" + "\n".join(self.problems) + "\n",
                                    encoding="utf-8")
                return False
            self.proc, self.url = start_server()
        return self.proc is not None

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()


def wait_until_up(proc: subprocess.Popen, url: str) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            with urllib.request.urlopen(url + "_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


# ------------------------------------------------------------------ window --
def _page(body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin:0; height:100vh; display:flex; flex-direction:column; align-items:center;
       justify-content:center; gap:18px; background:#080e19; color:#f2f6fb;
       font:15px 'Segoe UI', system-ui, sans-serif; text-align:center; }}
h1 {{ font-size:22px; margin:0; }} p {{ color:#a8b8ca; margin:0; max-width:520px; }}
code {{ color:#f2f6fb; user-select:all; }}
</style></head><body>{body}</body></html>"""


DEFAULT_ACCENT = "#52d5f2"  # branding.DEFAULT_THEME's primary color


def saved_theme() -> dict:
    """The team theme saved on the Team Theme page (branding.save_theme), read without
    loading Streamlit so the launch screen can show it straight away: its color and name.
    The default theme when nothing (valid) is saved."""
    theme = {"primary": DEFAULT_ACCENT, "name": ""}
    try:
        saved = json.loads((DATA_DIR / "appearance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return theme
    if isinstance(saved, dict):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", str(saved.get("primary", ""))):
            theme["primary"] = saved["primary"]
        if isinstance(saved.get("name"), str) and saved["name"] not in ("R6 Match Stats", "Siege / NECC"):  # the default's, now and before 1.6.0
            theme["name"] = saved["name"].strip()[:80]
    return theme


def readable_accent(color: str) -> str:
    """Lighten a school color enough to read on the dark background (as branding.readable_accent)."""
    return "#" + "".join(f"{max(int(color[n:n + 2], 16), 145):02x}" for n in (1, 3, 5))


# The launch screen: the app's icon (a crosshair around rising stat bars) draws itself, then a
# sweep circles it while the steps below fill in. launch_step() moves the steps along as the
# app really starts, and launch_ready() fades it all out before the dashboard loads.
SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
:root { --accent:@ACCENT@; --glow:@GLOW@; --bg:#080e19; --line:#293b51; --text:#f2f6fb; --dim:#a8b8ca; }
* { box-sizing:border-box; }
html, body { height:100%; margin:0; }
body { display:flex; align-items:center; justify-content:center; overflow:hidden; background:
       radial-gradient(ellipse at 50% 42%, var(--glow), transparent 55%),
       repeating-linear-gradient(0deg, transparent 0 31px, rgba(168,184,202,.035) 32px), var(--bg);
       color:var(--text); font:14px 'Segoe UI', system-ui, sans-serif; user-select:none; cursor:default; }
main { display:flex; flex-direction:column; align-items:center; gap:22px; transition:opacity .6s, transform .6s; }
body.ready main { opacity:0; transform:scale(1.04); }
svg { width:148px; height:148px; overflow:visible; }
.ring { fill:none; stroke:var(--accent); stroke-width:4.5; stroke-linecap:round; stroke-dasharray:239;
        stroke-dashoffset:239; transform:rotate(-90deg); transform-origin:60px 60px;
        animation:draw .9s cubic-bezier(.6,0,.3,1) forwards; }
.tick { stroke:var(--accent); stroke-width:4.5; stroke-linecap:round; opacity:0;
        animation:fade .3s ease-out .75s forwards; }
.bar { fill:var(--text); transform:scaleY(0); transform-box:fill-box; transform-origin:50% 100%;
       animation:rise .45s cubic-bezier(.3,1.4,.5,1) forwards; }
.bar.b1 { animation-delay:.9s; } .bar.b2 { animation-delay:1.02s; } .bar.b3 { fill:var(--accent); animation-delay:1.14s; }
.sweep { fill:none; stroke:var(--accent); stroke-width:2; stroke-linecap:round; stroke-dasharray:34 268;
         opacity:0; transform-origin:60px 60px; animation:fade .4s 1.3s forwards, spin 1.6s linear 1.3s infinite; }
h1 { margin:0; font:600 30px/1 Bahnschrift, 'Barlow Condensed', 'Arial Narrow', sans-serif; text-transform:uppercase;
     letter-spacing:.5em; opacity:0; animation:title .8s cubic-bezier(.2,.7,.2,1) .35s forwards; }
.sub { margin-top:-12px; color:var(--dim); font-size:12px; letter-spacing:.14em; text-transform:uppercase;
       opacity:0; animation:fade .6s .7s forwards; }
.steps { display:grid; grid-template-columns:repeat(3, 72px); gap:6px; margin-top:6px; opacity:0; animation:fade .5s .9s forwards; }
.steps i { height:3px; border-radius:2px; background:var(--line); position:relative; overflow:hidden; }
.steps i.done { background:var(--accent); }
.steps i.now::after { content:""; position:absolute; inset:0; width:40%;
                      background:linear-gradient(90deg, transparent, var(--accent), transparent); animation:scan 1.1s ease-in-out infinite; }
.status { min-height:18px; margin-top:-10px; color:var(--dim); font:12px ui-monospace, 'Cascadia Mono', Consolas, monospace;
          letter-spacing:.06em; opacity:0; animation:fade .5s 1s forwards; }
.slow { position:fixed; bottom:34px; left:0; right:0; text-align:center; color:var(--dim); font-size:12px;
        opacity:0; animation:fade .8s 14s forwards; }
.foot { position:fixed; bottom:12px; left:0; right:0; text-align:center; color:#5d6f84; font-size:11px; }
@keyframes draw { to { stroke-dashoffset:0; } }
@keyframes rise { to { transform:scaleY(1); } }
@keyframes fade { to { opacity:1; } }
@keyframes spin { to { transform:rotate(360deg); } }
@keyframes title { to { opacity:1; letter-spacing:.16em; } }
@keyframes scan { from { transform:translateX(-100%); } to { transform:translateX(250%); } }
@media (prefers-reduced-motion: reduce) {
  *, *::after { animation-duration:0s !important; animation-delay:0s !important; animation-iteration-count:1 !important; }
  .sweep { display:none; }
}
</style></head><body>
<main>
  <svg viewBox="0 0 120 120" aria-hidden="true">
    <circle class="sweep" cx="60" cy="60" r="48"/>
    <circle class="ring" cx="60" cy="60" r="38"/>
    <line class="tick" x1="60" y1="14" x2="60" y2="30"/><line class="tick" x1="60" y1="90" x2="60" y2="106"/>
    <line class="tick" x1="14" y1="60" x2="30" y2="60"/><line class="tick" x1="90" y1="60" x2="106" y2="60"/>
    <rect class="bar b1" x="43" y="62" width="9" height="16" rx="2"/>
    <rect class="bar b2" x="55.5" y="52" width="9" height="26" rx="2"/>
    <rect class="bar b3" x="68" y="42" width="9" height="36" rx="2"/>
  </svg>
  <h1>@APP_NAME@</h1>
  <div class="sub">@SUBTITLE@</div>
  <div class="steps"><i class="now"></i><i></i><i></i></div>
  <div class="status" id="status" role="status" aria-live="polite">@FIRST_STEP@</div>
</main>
<div class="slow">The first start after installing can take up to a minute while Windows checks the app.</div>
<div class="foot">@VERSION@ · Unofficial fan project, not affiliated with Ubisoft</div>
<script>
const STEPS = @STEP_NAMES@;
function launch_step(n) {
  document.querySelectorAll('.steps i').forEach((bar, i) => { bar.className = i < n ? 'done' : i === n ? 'now' : ''; });
  document.getElementById('status').textContent = STEPS[n] || '';
}
function launch_ready() {
  document.querySelectorAll('.steps i').forEach(bar => { bar.className = 'done'; });
  document.body.classList.add('ready');
}
</script>
</body></html>"""

# what the app is doing at each step of the launch screen
LAUNCH_STEPS = ("Checking the app's files", "Starting the stats engine", "Loading your dashboard")


def splash_page(theme: dict | None = None) -> str:
    """The launch screen, in the saved school theme's color, with its name under the app's."""
    theme = theme or saved_theme()
    accent = readable_accent(theme.get("primary") or DEFAULT_ACCENT)
    r, g, b = (int(accent[n:n + 2], 16) for n in (1, 3, 5))
    values = {
        "ACCENT": accent, "GLOW": f"rgba({r},{g},{b},.16)", "APP_NAME": html.escape(APP_NAME),
        "SUBTITLE": html.escape(theme.get("name") or "Rainbow Six Siege match stats"),
        "VERSION": html.escape(f"v{APP_VERSION}"), "FIRST_STEP": html.escape(LAUNCH_STEPS[0]),
        # "</" can't end the script early: the names are fixed, but keep it safe to edit
        "STEP_NAMES": json.dumps(LAUNCH_STEPS).replace("</", "<\\/"),
    }
    return re.sub(r"@([A-Z_]+)@", lambda m: values[m.group(1)], SPLASH)  # one pass: names can't inject tokens


def show_step(window, step: int) -> None:
    """Move the launch screen on to `step` (an index into LAUNCH_STEPS). Only cosmetic, so a
    window that can't run it yet just keeps showing the previous step."""
    try:
        window.evaluate_js(f"launch_step({step})")
    except Exception:
        pass


def finish_launch(window) -> None:
    """Fade the launch screen out, so the dashboard doesn't cut in abruptly."""
    try:
        window.evaluate_js("launch_ready()")
        time.sleep(FADE_SECONDS)
    except Exception:
        pass


def failed_page() -> str:
    return _page(f"<h1>{APP_NAME} couldn't start</h1><p>Close this window and open the app again. "
                 f"If it keeps happening, the details are in <code>{LOG_FILE}</code></p>")


TAMPERED = (f"{APP_NAME} won't start: some of its files were changed, added or removed since it was "
            "installed, so it may not be safe to run. Uninstall it, then reinstall it from its official "
            "download page.")


def tampered_page(problems: list[str]) -> str:
    listed = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in problems[:8])
    return _page(f"<h1>{APP_NAME} won't start</h1><p>{html.escape(TAMPERED)}</p>"
                 f"<ul style='text-align:left; color:#8b949e'>{listed}</ul>")


SMOKE_TEST_JS = """(() => {
  const main = document.querySelector('[data-testid="stMain"]') || document.querySelector('.stApp');
  const text = main ? main.innerText : '';
  const error = !!document.querySelector('[data-testid="stException"]');
  return JSON.stringify({title: document.title, url: location.href, error, text: text.slice(0, 4000)});
})()"""


# ------------------------------------------------------------------ docking --
def dock_request(args: list[str]) -> str | None:
    """What --dock asked for: LEFT, RIGHT, "off" (undocked), or None if it wasn't given."""
    if "--dock" in args:
        i = args.index("--dock")
        value = args[i + 1].casefold() if i + 1 < len(args) else ""
        if value in (LEFT, RIGHT, "off"):
            return value
    return None


def saved_dock() -> str | None:
    """The edge the app was docked to when it last closed (it reopens docked there), or None."""
    try:
        edge = json.loads((DATA_DIR / DOCK_STATE).read_text(encoding="utf-8")).get("dock")
    except (OSError, ValueError, AttributeError):
        return None
    return edge if edge in (LEFT, RIGHT) else None


def save_dock(edge: str | None) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / DOCK_STATE).write_text(json.dumps({"dock": edge}), encoding="utf-8")
    except OSError:
        pass


def take_window_commands() -> list[dict]:
    """The window commands waiting (app_info.send_window_command), oldest first; each is removed
    as it's taken, so it's done once."""
    folder = DATA_DIR / WINDOW_COMMANDS
    commands = []
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            command = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            command = None
        try:
            file.unlink()
        except OSError:
            continue  # still being written or already taken: leave it
        if isinstance(command, dict):
            commands.append(command)
    return commands


class Docking:
    """Docks the app window to the left or right edge of its screen (windows_shell.AppBar), where it
    shows the compact docked page (scripts/dock.py), and back to a full window. The choice is
    remembered, so the app reopens the way it was closed."""

    def __init__(self, window) -> None:
        self.window = window
        self.url = ""  # the app's address, once its server is up
        self.edge: str | None = None
        self._bar = None
        self._lock = threading.Lock()

    def page(self) -> str:
        """The address the window should show: the docked page while docked, else the app."""
        return f"{self.url}?view=dock&edge={self.edge}" if self.edge else self.url

    def _appbar(self):
        if self._bar is None:
            from windows_shell import AppBar

            self._bar = AppBar(int(self.window.native.Handle.ToInt64()))
        return self._bar

    def set(self, edge: str | None, load: bool = True) -> None:
        """Dock to LEFT or RIGHT, or undock (None); `load` shows the matching page."""
        edge = edge if edge in (LEFT, RIGHT) else None
        with self._lock:
            if edge == self.edge:
                return
            if edge:
                self._appbar().dock(edge, DOCK_WIDTH)
            elif self._bar is not None:
                self._bar.undock()
            self.edge = edge
            save_dock(edge)
            if load and self.url:
                self.window.load_url(self.page())

    def release(self) -> None:
        """Give the screen strip back as the app closes, but remember it was docked."""
        with self._lock:
            if self._bar is not None and self._bar.edge:
                try:
                    self._bar.undock()
                except Exception:
                    pass

    def follow_commands(self) -> None:
        """Carry out the window commands the pages and the jump list send, for as long as the app runs."""
        while True:
            for command in take_window_commands():
                try:
                    self.set(command.get("edge") if command.get("command") == "dock" else None)
                except Exception as e:
                    log(f"Couldn't {command.get('command')} the window: {e!r}")
            time.sleep(0.4)


JUMP_LIST = {"state": "not set"}  # how setting it went, for the smoke test


def set_jump_list_quietly() -> None:
    """Add the jump list's tasks (Dock to the right, ...); a failure only means no tasks."""
    try:
        from windows_shell import set_jump_list

        set_jump_list(APP_ID, sys.executable, JUMP_LIST_TASKS)
        JUMP_LIST["state"] = "set"
    except Exception as e:
        JUMP_LIST["state"] = repr(e)
        log(f"Couldn't set the jump list: {e!r}")


# ------------------------------------------------------------------- window --
def run_window(server: Server, dock: str | None = None) -> int:
    """The app window. `dock` (from --dock) docks it on opening; otherwise it opens the way it was
    last closed, docked or not."""
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True  # the report's CSV, JSON and TXT buttons
    smoke_file = os.environ.get("R6_SMOKE_TEST")
    result = {"ok": False}
    docking: Docking | None = None
    take_window_commands()  # anything left over from a crash is stale

    def load(window) -> None:
        nonlocal docking
        window.events.shown.wait(15)  # pywebview starts this before it creates the window
        opened = time.monotonic()
        if IS_WINDOWS:
            docking = Docking(window)
            threading.Thread(target=set_jump_list_quietly, name="jump-list", daemon=True).start()
            edge = (None if dock == "off" else dock) if dock else (None if smoke_file else saved_dock())
            try:
                docking.set(edge, load=False)  # docks now, so the launch screen shows where the app will be
            except Exception as e:
                log(f"Couldn't dock the window: {e!r}")
        # the check runs while the launch screen shows its first step, before any server code
        if not server.start():
            window.load_html(tampered_page(server.problems))
            if smoke_file:
                result["tampered"] = server.problems
                window.destroy()
            return
        show_step(window, 1)
        if not wait_until_up(server.proc, server.url):
            window.load_html(failed_page())
            return
        show_step(window, 2)
        time.sleep(max(0.0, MIN_LAUNCH_SECONDS - (time.monotonic() - opened)))
        finish_launch(window)
        if docking:
            docking.url = server.url
            window.load_url(docking.page())
            threading.Thread(target=docking.follow_commands, name="window-commands", daemon=True).start()
        else:
            window.load_url(server.url)
        if smoke_file:
            result.update(smoke_test(window))
            if docking and result["ok"]:
                result.update(smoke_test_docking(window, docking))
            window.destroy()

    # the minimum size is below the docked panel's, so it never stops the window becoming one
    window = webview.create_window(APP_NAME, html=splash_page(), width=1440, height=900,
                                   min_size=(DOCK_WIDTH - 60, 480), background_color="#080e19", text_select=True)
    def closing() -> None:  # returns None: returning False would keep the window open
        if docking:
            docking.release()

    window.events.closing += closing
    try:
        webview.start(load, window, private_mode=False, storage_path=str(DATA_DIR / "webview"))
    finally:
        if docking:
            docking.release()
    if smoke_file:
        Path(smoke_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0 if result["ok"] else 1
    return 0


def _read_page(window) -> dict:
    try:
        return json.loads(window.evaluate_js(SMOKE_TEST_JS) or "{}")
    except Exception as e:  # the page is still loading
        return {"js_error": repr(e)}


def _screenshot(suffix: str = "") -> str | None:
    """Save the screen next to the smoke test's result; the error, if it couldn't."""
    try:
        from PIL import ImageGrab

        ImageGrab.grab().save(os.environ["R6_SMOKE_TEST"] + suffix + ".png")
    except Exception as e:  # no desktop to capture
        return repr(e)
    return None


def smoke_test(window) -> dict:
    """Wait for the report page to render in the window and report what it shows."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    seen: dict = {}
    while time.monotonic() < deadline:
        seen = _read_page(window)
        if seen.get("error"):
            break
        if dashboard_is_rendered(seen.get("text", "")):
            time.sleep(2)  # let the page finish painting for the screenshot
            error = _screenshot()
            if error:
                seen["screenshot_error"] = error
            return {"ok": True, **seen}
        time.sleep(1)
    return {"ok": False, **seen}


def smoke_test_docking(window, docking: Docking) -> dict:
    """Dock the window to the right, check it took its strip of the screen and shows the docked
    page, then undock it and check the screen is given back. Also reports the app's identity."""
    from windows_shell import app_id, window_rect, work_area

    deadline = time.monotonic() + 20
    while JUMP_LIST["state"] == "not set" and time.monotonic() < deadline:  # it's set on its own thread
        time.sleep(0.5)
    report: dict = {"app_id": app_id(), "jump_list": JUMP_LIST["state"], "work_area": work_area()}
    try:
        docking.set(RIGHT)
        report["docked_rect"] = window_rect(docking._bar.hwnd)
        report["docked_work_area"] = work_area()
        deadline, page = time.monotonic() + 60, {}
        while time.monotonic() < deadline and not dock_is_rendered(page.get("text", "")):
            time.sleep(1)
            page = _read_page(window)
        report["docked_page"] = page.get("text", "")[:600]
        time.sleep(2)
        report["docked_screenshot_error"] = _screenshot(".docked")
        docking.set(None)
        time.sleep(1)
        report["undocked_rect"] = window_rect(docking._bar.hwnd)
        report["undocked_work_area"] = work_area()
    except Exception as e:
        report["dock_error"] = repr(e)
    right = report["work_area"][2]
    docked = report.get("docked_work_area")
    report["dock_ok"] = bool(
        report.get("app_id") == APP_ID and report["jump_list"] == "set"
        and docked and docked[2] < right  # the strip left the work area...
        and report.get("docked_rect", (0, 0, 0, 0))[2] >= right  # ...and the window sits in it
        and dock_is_rendered(report.get("docked_page", ""))
        and report.get("undocked_work_area") == report["work_area"]  # ...and was given back
    )
    return {"ok": report["dock_ok"], "docking": report}


def dashboard_is_rendered(text: str) -> bool:
    """Recognize the current page name while preserving older report wording.
    Themes can capitalize headings, and the window reports text as shown ("DASHBOARD")."""
    text = text.casefold()
    return "dashboard" in text or "match report" in text


def dock_is_rendered(text: str) -> bool:
    """The docked page (scripts/dock.py) is showing: it has the Full window button."""
    return "full window" in text.casefold()


def run_in_browser(server: Server) -> int:
    """Fallback without WebView2: open the default browser, and keep the server
    running until the user says to quit."""
    import webbrowser

    if not server.start():
        message_box(TAMPERED + "\n\n" + "\n".join(server.problems[:8]))
        return 1
    if not wait_until_up(server.proc, server.url):
        message_box(f"{APP_NAME} couldn't start. The details are in {LOG_FILE}")
        return 1
    webbrowser.open(server.url)
    message_box(f"{APP_NAME} is open in your web browser at {server.url}\n\nClick OK to quit the app.")
    return 0


def message_box(text: str) -> None:
    if IS_WINDOWS:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)  # MB_ICONINFORMATION
    else:
        print(text)
        try:
            input()
        except EOFError:
            threading.Event().wait()


# ---------------------------------------------------------- single instance --
def already_running() -> bool:
    """If the app is already open, bring its window to the front and return True."""
    if not IS_WINDOWS:
        return False
    import ctypes

    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    already = kernel32.CreateMutexW(None, False, "Local\\R6MatchStats") and kernel32.GetLastError() == 183
    if already:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(None, APP_NAME)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    return bool(already)


def main() -> int:
    hide_console_windows()  # first, before anything can start a program
    if len(sys.argv) >= 4 and sys.argv[1] == "--serve":
        return serve(int(sys.argv[2]), int(sys.argv[3]))
    dock = dock_request(sys.argv[1:])
    if not os.environ.get("R6_SMOKE_TEST") and already_running():
        if dock:  # e.g. the jump list's "Dock to the right": the open window does it
            send_window_command("undock") if dock == "off" else send_window_command("dock", edge=dock)
        return 0
    if IS_WINDOWS:
        try:
            from windows_shell import set_app_id

            set_app_id(APP_ID)  # before any window opens
        except Exception as e:
            log(f"Couldn't set the app's Windows identity: {e!r}")

    server = Server()
    try:
        if os.environ.get("R6_NO_WINDOW"):
            return run_in_browser(server)
        try:
            return run_window(server, dock)
        except Exception as e:  # no WebView2 runtime, or it failed to start
            log(f"\nThe app window couldn't open ({e!r}); using the web browser instead.")
            if os.environ.get("R6_SMOKE_TEST"):
                raise
            return run_in_browser(server)
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
