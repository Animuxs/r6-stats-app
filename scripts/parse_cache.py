"""Parsed replays kept on disk, so a match is read from its replay files once per PC instead of
once per session: opening the app again, or Team Analytics looking through the whole replay
folder, loads each match in about a millisecond instead of running r6-dissect (most of a second).

A match is stored under a key made of its .rec files (path, size and modified time) and of the
parser itself (this app's parser.py and the r6-dissect program), so a replay that changes, or a
new version of the app, reads it again. Entries are gzipped JSON, never pickle: a file planted in
the folder can't run code. The least recently used are deleted past MAX_ENTRIES."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import parser as replay_parser
from stats_db import default_path

FOLDER_NAME = "parsed-replays"
FORMAT = 1           # change when what's stored changes
MAX_ENTRIES = 2000   # matches kept, about 6 KB each


def folder() -> Path:
    """Where the parsed matches are kept: next to the stats database on this PC."""
    return default_path().parent / FOLDER_NAME


@lru_cache(maxsize=1)
def parser_fingerprint() -> str:
    """Changes with the parser: this app's parser.py and the r6-dissect program it runs."""
    h = hashlib.sha256(f"format {FORMAT}".encode())
    with contextlib.suppress(OSError):
        h.update(Path(replay_parser.__file__).read_bytes())
    binary = replay_parser.R6_DISSECT_BIN
    if binary:
        with contextlib.suppress(OSError):
            stat = Path(binary).stat()
            h.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return h.hexdigest()


def key(rec_paths: Iterable[str]) -> str | None:
    """The match's cache key, or None when one of its files can't be looked at (then it isn't cached)."""
    h = hashlib.sha256(parser_fingerprint().encode())
    paths = sorted(str(Path(p).resolve()) for p in rec_paths)
    if not paths:
        return None
    for p in paths:
        try:
            stat = os.stat(p)
        except OSError:
            return None
        h.update(f"{p}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return h.hexdigest()


def load(rec_paths: Iterable[str], where: Path | None = None) -> tuple[dict, dict, list[str]] | None:
    """The match parsed earlier from these files, or None if it isn't kept (or the entry is damaged)."""
    k = key(rec_paths)
    if k is None:
        return None
    path = (where or folder()) / f"{k}.json.gz"
    try:
        stored = json.loads(gzip.decompress(path.read_bytes()))
        match, raw, warnings = stored["match"], stored["raw"], stored["warnings"]
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None
    with contextlib.suppress(OSError):
        os.utime(path)  # recently used: kept when older ones are deleted
    return match, raw, list(warnings)


def save(rec_paths: Iterable[str], result: tuple[dict, dict, list[str]], where: Path | None = None) -> None:
    """Keep a parsed match. Never fails the caller: a match that can't be kept is just parsed again."""
    k = key(rec_paths)
    if k is None:
        return
    target = where or folder()
    try:
        target.mkdir(parents=True, exist_ok=True)
        data = gzip.compress(json.dumps({"match": result[0], "raw": result[1], "warnings": list(result[2])}).encode())
        fd, tmp = tempfile.mkstemp(dir=target, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target / f"{k}.json.gz")  # whole or not at all, even if the app closes mid-write
    except (OSError, TypeError, ValueError):
        with contextlib.suppress(OSError, UnboundLocalError):
            os.remove(tmp)
        return
    prune(target)


def prune(where: Path | None = None, keep: int = MAX_ENTRIES) -> int:
    """Delete the least recently used entries past `keep`, and leftovers of interrupted saves; how many went."""
    target = where or folder()
    try:
        entries = sorted(target.glob("*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        leftovers = list(target.glob("*.tmp"))
    except OSError:
        return 0
    gone = 0
    for path in entries[keep:] + leftovers:
        with contextlib.suppress(OSError):
            path.unlink()
            gone += 1
    return gone


def parse(rec_paths: list[str], parse_match: Callable[[list[str]], Any] = replay_parser.parse_match,
          where: Path | None = None) -> tuple[dict, dict, list[str]]:
    """parse_match, from the cache when this match was read before. Errors aren't kept: a replay that
    couldn't be read is tried again next time (the game may still have been writing it)."""
    found = load(rec_paths, where)
    if found is not None:
        return found
    result = parse_match(rec_paths)
    save(rec_paths, result, where)
    return result
