"""Tests for release_notes.py. Run from the repo root:  python -m unittest discover -s desktop"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from release_notes import END, START, downloads_section, with_downloads

REPO, TAG = "julio208920/r6-dissect", "v1.6.5"
FILES = ["dist/R6MatchStats-Setup.exe", "dist/R6MatchStats-Windows.zip", "dist/R6MatchStats-Browser.html",
         "dist/SHA256SUMS.txt"]
BASE = "https://github.com/julio208920/r6-dissect/releases/download/v1.6.5/"


class TestReleaseNotes(unittest.TestCase):

    def test_links_each_file_in_order(self):
        section = downloads_section(REPO, TAG, ["SHA256SUMS.txt", "R6MatchStats-Browser.html",
                                                "R6MatchStats-Setup.exe", "R6MatchStats-Windows.zip"])
        links = [line.split("](")[1].split(")")[0] for line in section.splitlines() if "](" in line]
        self.assertEqual(links, [BASE + n for n in ("R6MatchStats-Setup.exe", "R6MatchStats-Windows.zip",
                                                    "R6MatchStats-Browser.html", "SHA256SUMS.txt")])
        self.assertTrue(section.startswith(START + "\n## Downloads"))
        self.assertTrue(section.endswith(END))

    def test_signature_and_other_files_go_last(self):
        section = downloads_section(REPO, TAG, ["extra.txt", "SIGNATURE.txt", "R6MatchStats-Setup.exe"])
        rows = [line for line in section.splitlines() if "](" in line]
        self.assertEqual([r.split("[")[1].split("]")[0] for r in rows],
                         ["R6MatchStats-Setup.exe", "SIGNATURE.txt", "extra.txt"])
        self.assertTrue(rows[2].startswith("| extra.txt |"))

    def test_goes_above_the_notes_and_replaces_itself(self):
        notes = "R6 Match Stats 1.6.5.\n\nNew:\n- Map pool\n"
        once = with_downloads(notes, REPO, TAG, ["R6MatchStats-Setup.exe"])
        self.assertTrue(once.startswith(START))
        self.assertTrue(once.endswith("\n\nR6 Match Stats 1.6.5.\n\nNew:\n- Map pool\n"))
        twice = with_downloads(once, REPO, TAG, ["R6MatchStats-Setup.exe", "SHA256SUMS.txt"])
        self.assertEqual(twice.count(START), 1)
        self.assertIn("SHA256SUMS.txt", twice)
        self.assertTrue(twice.endswith("- Map pool\n"))

    def test_empty_notes(self):
        self.assertEqual(with_downloads("", REPO, TAG, ["R6MatchStats-Setup.exe"]),
                         downloads_section(REPO, TAG, ["R6MatchStats-Setup.exe"]) + "\n")

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            notes = Path(folder) / "notes.md"
            notes.write_text("﻿The notes\n", encoding="utf-8")  # as PowerShell's Out-File writes it
            script = Path(__file__).with_name("release_notes.py")
            subprocess.run([sys.executable, str(script), REPO, TAG, str(notes), *FILES], check=True)
            text = notes.read_text(encoding="utf-8")
            self.assertIn(f"[R6MatchStats-Setup.exe]({BASE}R6MatchStats-Setup.exe)", text)
            self.assertTrue(text.endswith("\n\nThe notes\n"))
            self.assertNotIn("﻿", text)
            self.assertNotEqual(subprocess.run([sys.executable, str(script), REPO], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
