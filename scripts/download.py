"""
download.py
===========
The "Get the Windows app" page: download link, install steps, updates, the browser
edition, and where Siege keeps its replays.
"""

from __future__ import annotations

import streamlit as st

import app_info
from app_info import (APP_NAME, APP_VERSION, HOW_IT_WORKS, NOTICE, RELEASES_URL, WINDOWS_INSTALLER, WINDOWS_ZIP,
                      is_windows_app, version_tuple)
from ui import md
from tab import Tab


@st.cache_data(ttl=600, show_spinner=False)
def latest_release() -> dict | None | str:
    """The newest Windows app release, None if there's none yet, or "unknown" if
    GitHub couldn't be reached (then the fixed /releases/latest links are used)."""
    try:
        return app_info.latest_release()
    except (OSError, ValueError):
        return "unknown"


class DownloadTab(Tab):
    """The Windows app's download, install steps and updates, the browser edition, and where replays are."""

    title, name = 'Get the Windows app', 'download'

    def render(self) -> None:
        release = latest_release()
        installer_url = release["installer"] if isinstance(release, dict) else app_info.WINDOWS_DOWNLOAD_URL
        zip_url = (release.get("zip") or app_info.WINDOWS_ZIP_URL) if isinstance(release, dict) else app_info.WINDOWS_ZIP_URL
        notes_url = release["url"] if isinstance(release, dict) else RELEASES_URL
        # releases before 1.6.0 don't have the browser edition
        browser_url = release.get("browser") if isinstance(release, dict) else app_info.BROWSER_EDITION_URL


        if is_windows_app():
            st.success(f"You're using the Windows app, version {APP_VERSION}.", icon="✅")
            if isinstance(release, dict) and version_tuple(release["version"]) > version_tuple(APP_VERSION):
                st.info(f"Version {release['version']} is available.", icon="🆕")
                st.link_button(f"⬇ Download version {release['version']}", installer_url, type="primary")
                st.caption("Run the downloaded installer: it closes this app, updates it and keeps your shortcuts. "
                           f"[What's new]({notes_url})")
            elif isinstance(release, dict):
                st.caption("This is the newest version.")
            else:
                st.link_button("Check for a newer version", RELEASES_URL)
        elif release is None:
            st.write(
                f"{APP_NAME} also runs as an app on your PC. It reads your Siege replays straight from the "
                "game's folder, so there's nothing to zip or upload, and your replays never leave your computer."
            )
            st.warning("The Windows app hasn't been published yet. Check back soon.", icon="⏳")
            st.link_button("See releases on GitHub", f"https://github.com/{app_info.GITHUB_REPO}/releases")
        else:
            st.write(
                f"{APP_NAME} also runs as an app on your PC. It reads your Siege replays straight from the "
                "game's folder, so there's nothing to zip or upload, and your replays never leave your computer."
            )
            version = f"version {release['version']} · " if isinstance(release, dict) else ""
            st.link_button("⬇ Download for Windows", installer_url, type="primary")
            st.caption(f"{WINDOWS_INSTALLER} · {version}Windows 10 or 11 (64-bit) · about 65 MB · "
                       f"[release notes]({notes_url})")

            signer = release.get("signer") if isinstance(release, dict) else None
            if signer:
                # signed: Windows names the publisher, but SmartScreen can still hold back a new certificate
                protected = (f"2. Windows may show **Windows protected your PC** while the app's certificate is new to "
                             f"it. Click **More info**, check the publisher is **{md(signer['name'])}**, then click "
                             "**Run anyway**.\n")
            else:
                protected = ("2. If Windows shows **Windows protected your PC**, click **More info**, then **Run anyway**. "
                             "The app isn't code-signed yet, so Windows doesn't recognize it.\n")
            st.subheader("Install")
            st.markdown(
                f"1. Download **{WINDOWS_INSTALLER}** above and open it.\n"
                + protected +
                "3. Click through the installer. It doesn't need an administrator, and it adds "
                f"**{APP_NAME}** to the Start menu (and, if you like, the desktop).\n"
                f"4. Open **{APP_NAME}** from the Start menu. It opens in its own window and shows your latest match."
            )
            st.caption(f"To uninstall, go to **Settings > Apps > Installed apps**, find {APP_NAME}, and choose "
                       f"**Uninstall**. Prefer no installer? Get the [portable zip]({zip_url}) ({WINDOWS_ZIP}): "
                       "extract it and run **R6MatchStats.exe** from the R6MatchStats folder.")

            st.subheader("Check your download")
            st.markdown(
                "Only download the app from this page or its GitHub releases. To make sure your copy is the "
                "one that was published and wasn't changed on the way, compare its SHA-256 fingerprint with "
                "the published one. In PowerShell, in the folder you downloaded it to, run:"
            )
            st.code(f"Get-FileHash .\\{WINDOWS_INSTALLER}", language="powershell")
            sha256 = release.get("sha256") if isinstance(release, dict) else None
            if sha256:
                st.markdown("The **Hash** it prints should be exactly:")
                st.code(sha256.upper(), language=None)
            else:
                st.markdown(f"The **Hash** it prints should match the one for {WINDOWS_INSTALLER} in "
                            f"**{app_info.CHECKSUMS}** on the [release page]({notes_url}).")
            if signer:
                st.markdown(f"It's also code-signed by **{md(signer['name'])}**. Right-click the installer, choose "
                            "**Properties > Digital Signatures**, then **Details > View Certificate > Details**: its "
                            "**Thumbprint** should be:")
                st.code(signer["thumbprint"], language=None)
            st.caption("Every time it opens, the app also checks that none of its own files were changed, added "
                       "or removed since it was installed, and refuses to run if they were.")

        if browser_url:
            st.subheader("Use it in your browser")
            st.write("Not on Windows, or can't install apps? The browser edition is the app as one web page: download it, "
                     "open it in any browser, and drop a match's replay files on it. Your replays are read right in the "
                     "browser tab and never uploaded. It has the scoreboards, the round-by-round breakdown and League & "
                     "Team, but not match history, Ask or the season tracker.")
            st.link_button("⬇ Download the browser edition", browser_url)
            st.caption(f"{app_info.BROWSER_EDITION} · about 13 MB · any modern browser. The first replay you open needs "
                       "an internet connection, to load Python into the page once.")

        st.subheader("Where are my replays?")
        st.markdown(
            "Siege saves each match you play in a **MatchReplay** folder inside the game's install folder, "
            "one folder per match. The app finds it automatically for Steam and Ubisoft Connect installs. "
            "Otherwise, paste the path. The usual places are:\n"
            "- Steam: `C:\\Program Files (x86)\\Steam\\steamapps\\common\\Tom Clancy's Rainbow Six Siege\\MatchReplay`\n"
            "- Ubisoft Connect: `C:\\Program Files (x86)\\Ubisoft\\Ubisoft Game Launcher\\games\\"
            "Tom Clancy's Rainbow Six Siege\\MatchReplay`\n\n"
            "No replays there? Make sure **Match Replay** is turned on in the game's options, then play a match."
        )

        st.subheader("About this app")
        st.markdown(
            f"{NOTICE}\n\n{HOW_IT_WORKS}\n\n"
            "**Your data:** the Windows app works on your PC, and the only thing it asks the internet for is "
            "whether GitHub has a newer version. On the website, uploaded replays are used only to build your "
            "report, and they're deleted once they've gone unused for an hour. Nothing is kept or shared."
        )


DownloadTab().run()
