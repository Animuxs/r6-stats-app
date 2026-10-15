"""
app_info.py
===========
Name, version and links shared by the website, the Windows app and its build,
plus where the app is running (public website, Windows app, or a source checkout).
"""

from __future__ import annotations

import ipaddress
import itertools
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent

APP_NAME = "R6 Match Stats"
# shown in the app, on the download page and in the installer (Ubisoft's EULA 1.3.j: no implied endorsement)
NOTICE = (f"{APP_NAME} is an unofficial fan project, free to use. It isn't made, endorsed or supported by "
          "Ubisoft. Rainbow Six and Ubisoft are trademarks of Ubisoft Entertainment.")
HOW_IT_WORKS = ("It only reads match replay files the game has already saved. It never connects to the game, "
                "Ubisoft's servers or your Ubisoft account, never reads or changes the game while it runs, "
                "and gives no advantage in a match.")
# the Windows build stamps the release's version (its tag without the "v") next to this file
_stamped_version = _HERE / "version.txt"
APP_VERSION = (_stamped_version.read_text().strip() if _stamped_version.is_file() else "") or "1.5.0"

# the Windows app, as attached to each GitHub release by .github/workflows/windows-app.yaml
WINDOWS_INSTALLER = "R6MatchStats-Setup.exe"
WINDOWS_ZIP = "R6MatchStats-Windows.zip"  # the portable version: no install, run from any folder
CHECKSUMS = "SHA256SUMS.txt"  # SHA-256 of both, written by desktop/build.ps1
SIGNATURE = "SIGNATURE.txt"  # who code-signed them, written by desktop/build.ps1 when it signs
DEFAULT_REPO = "julio208920/r6-dissect"


def github_repo() -> str:
    """"owner/name" of the GitHub repository whose releases carry the Windows app:
    $R6_GITHUB_REPO, else the repo.txt the Windows build stamps next to this file,
    else this checkout's origin remote (so a fork links to its own releases)."""
    if os.environ.get("R6_GITHUB_REPO"):
        return os.environ["R6_GITHUB_REPO"].strip()
    stamped = _HERE / "repo.txt"
    if stamped.is_file() and stamped.read_text().strip():
        return stamped.read_text().strip()
    git_config = _HERE.parent / ".git" / "config"
    if git_config.is_file():
        origin = re.search(r'\[remote "origin"\][^\[]*?\burl\s*=\s*(\S+)', git_config.read_text())
        if origin:
            slug = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", origin.group(1))
            if slug:
                return slug.group(1)
    return DEFAULT_REPO


GITHUB_REPO = github_repo()
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
WINDOWS_DOWNLOAD_URL = f"{RELEASES_URL}/download/{WINDOWS_INSTALLER}"
WINDOWS_ZIP_URL = f"{RELEASES_URL}/download/{WINDOWS_ZIP}"


def _get(url: str, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": APP_NAME})
    return urllib.request.urlopen(request, timeout=5)


def release_version(tag: str) -> str:
    """The version number in a release tag: "v1.2.0", "app-v1.2.0" and "1.2.0" are all "1.2.0"."""
    found = re.search(r"\d+(?:\.\d+){0,3}", tag)
    return found.group(0) if found else tag


def latest_release(repo: str = GITHUB_REPO) -> dict | None:
    """The newest published release that carries the Windows app, from the GitHub API:
    {"version", "url", "installer", "zip", "sha256", "signer"} ("zip", the installer's
    "sha256" and "signer", {"name", "thumbprint"} of its code signature, may be None). None if
    the repo has no such release yet. Raises OSError if GitHub can't be reached."""
    with _get(f"https://api.github.com/repos/{repo}/releases?per_page=20") as response:
        releases = json.load(response)
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        if WINDOWS_INSTALLER in assets:
            return {"version": release_version(release["tag_name"]), "url": release["html_url"],
                    "installer": assets[WINDOWS_INSTALLER], "zip": assets.get(WINDOWS_ZIP),
                    "sha256": _published_sha256(assets.get(CHECKSUMS), WINDOWS_INSTALLER),
                    "signer": _published_signer(assets.get(SIGNATURE))}
    return None


def _published_signer(signature_url: str | None) -> dict | None:
    """{"name", "thumbprint"} of the certificate a release was signed with, from its SIGNATURE.txt
    ("Signed by: NAME" and "Thumbprint: 40 hex digits"), if it has one."""
    if not signature_url:
        return None
    try:
        with _get(signature_url, accept="application/octet-stream") as response:
            text = response.read(4096).decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    fields = dict(line.strip().partition(": ")[::2] for line in text.splitlines() if ": " in line)
    name, thumbprint = fields.get("Signed by", "").strip(), fields.get("Thumbprint", "").strip().upper()
    if name and re.fullmatch(r"[0-9A-F]{40}", thumbprint):
        return {"name": name[:120], "thumbprint": thumbprint}
    return None


def _published_sha256(checksums_url: str | None, name: str) -> str | None:
    """The SHA-256 a release's SHA256SUMS.txt lists for `name`, if it has one."""
    if not checksums_url:
        return None
    try:
        with _get(checksums_url, accept="application/octet-stream") as response:
            text = response.read(64 * 1024).decode("ascii", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        digest, _, file = line.strip().partition("  ")
        if file == name and re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    return None


def version_tuple(version: str) -> tuple[int, ...]:
    """"1.10.2" -> (1, 10, 2), for comparing versions; non-numeric parts count as 0."""
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.+-]", release_version(version))[:3])


def is_windows_app() -> bool:
    """Running as the packaged Windows app (desktop/launcher.py sets this)."""
    return os.environ.get("R6_DESKTOP") == "1"


def can_dock() -> bool:
    """The app's window can be docked to the edge of the screen: the Windows app, on Windows."""
    return is_windows_app() and sys.platform == "win32"


def desktop_data_dir() -> Path:
    """Where the Windows app keeps its log, settings and stats: %LOCALAPPDATA%\\R6MatchStats."""
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "R6MatchStats"


# commands for the Windows app's window (desktop/launcher.py), one small JSON file each
WINDOW_COMMANDS = "window-commands"
WINDOW_COMMAND_NAMES = ("dock", "undock")
_command_numbers = itertools.count()  # Windows' clock can give two quick commands the same time


def send_window_command(command: str, **args: str) -> None:
    """Ask the Windows app's window to do something: "dock" (edge="left" or "right") or "undock".
    The pages run in the app's server process, so they can't reach the window directly: each
    command is written as a file that the window picks up (and deletes) within half a second."""
    if command not in WINDOW_COMMAND_NAMES:
        raise ValueError(f"unknown window command: {command}")
    folder = desktop_data_dir() / WINDOW_COMMANDS
    folder.mkdir(parents=True, exist_ok=True)
    # sorts oldest first; the counter keeps two commands from one process apart, the process ID two processes'
    name = f"{time.time_ns():020d}-{next(_command_numbers):06d}-{os.getpid()}"
    temporary = folder / f"{name}.tmp"
    temporary.write_text(json.dumps({"command": command, **args}), encoding="utf-8")
    temporary.replace(folder / f"{name}.json")  # appears whole, never half-written


def is_public_host() -> bool:
    """Running on a public web host: Streamlit Community Cloud (apps live under
    /mount/src), Hugging Face Spaces, or anywhere R6_HOSTED is set."""
    return bool(os.environ.get("R6_HOSTED") or os.environ.get("SPACE_ID")) or _HERE.as_posix().startswith("/mount/src/")


def is_loopback(ip: str | None) -> bool:
    """Whether a visitor's IP (st.context.ip_address) is this machine. Streamlit
    reports None for ::1 and 127.0.0.1, but IPv4 visitors on a dual-stack server
    show up as ::ffff:127.0.0.1."""
    if ip is None:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _drop_connection_resets(record: logging.LogRecord) -> bool:
    return not (record.exc_info and isinstance(record.exc_info[1], ConnectionResetError))


def quiet_windows_connection_resets() -> None:
    """On Windows, asyncio logs a scary but harmless ConnectionResetError traceback
    whenever a browser drops a connection (loading the page, refreshing, closing
    the tab). Call before the server starts to hide just those."""
    if sys.platform == "win32":
        logging.getLogger("asyncio").addFilter(_drop_connection_resets)  # no-op if already added
