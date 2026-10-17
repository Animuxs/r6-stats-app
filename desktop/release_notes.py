"""
release_notes.py
================
Puts a Downloads section at the top of a release's notes, with a link to each file the Windows app
workflow attached, because GitHub lists a release's files by name and the command-line parser's
r6-dissect-* files come before the app's. The section sits between two markers, so running it
again (a re-run of the workflow) replaces it instead of adding a second one.

    python desktop/release_notes.py OWNER/REPO TAG notes.md R6MatchStats-Setup.exe ...

rewrites notes.md (the release's current notes, from `gh release view TAG --json body -q .body`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote

START = "<!-- downloads -->"
END = "<!-- /downloads -->"

# (what it is, what it's for), in the order they're listed; any other file goes last
FILES = {
    "R6MatchStats-Setup.exe": ("**Windows installer** (recommended)", "Installs the app with a Start menu shortcut"),
    "R6MatchStats-Windows.zip": ("**Windows portable**", "Unzip and run R6MatchStats.exe, no install"),
    "R6MatchStats-Browser.html": ("**Browser edition**", "Open in a browser, nothing to install"),
    "SHA256SUMS.txt": ("Checksums", "To check the downloads are intact"),
    "SIGNATURE.txt": ("Signature", "Who signed the app"),
}
PARSER_NOTE = ("The `r6-dissect-…` files further down are the command-line replay parser for developers. "
               "Most people don't need them.")


def downloads_section(repo: str, tag: str, files: list[str]) -> str:
    """The Downloads section, a table with a link to each of `files` on release `tag`."""
    base = f"https://github.com/{repo}/releases/download/{quote(tag)}"
    order = list(FILES)
    names = sorted(dict.fromkeys(files), key=lambda n: (order.index(n) if n in order else len(order), n))
    rows = [f"| {FILES.get(n, (n, ''))[0]} | [{n}]({base}/{quote(n)}) | {FILES.get(n, ('', ''))[1]} |" for n in names]
    return "\n".join([START, "## Downloads", "", "| | File | |", "|---|---|---|", *rows, "", PARSER_NOTE, END])


def with_downloads(notes: str, repo: str, tag: str, files: list[str]) -> str:
    """`notes` with the Downloads section at the top, replacing one added before."""
    rest = re.sub(re.escape(START) + r".*?" + re.escape(END), "", notes, flags=re.S).strip()
    return downloads_section(repo, tag, files) + ("\n\n" + rest if rest else "") + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    repo, tag, path, *names = sys.argv[1:]
    notes_file = Path(path)
    text = notes_file.read_text(encoding="utf-8-sig") if notes_file.is_file() else ""
    notes_file.write_text(with_downloads(text, repo, tag, [Path(n).name for n in names]), encoding="utf-8")
