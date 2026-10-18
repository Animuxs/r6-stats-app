"""The parsed-replay cache: a match is parsed once per PC, read again only when its files or the
parser change, never trusted when damaged, and kept to a fixed number of matches."""

from __future__ import annotations

import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import parse_cache
from parser import ReplayParseError
from sample_data import SAMPLE_MATCH


class TestParseCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = self.tmp / "cache"
        self.match = self.tmp / "Match-2026-09-20_19-00-00-1"
        self.match.mkdir()
        self.recs = []
        for n in (1, 2):
            rec = self.match / f"Match-R0{n}.rec"
            rec.write_bytes(b"round %d" % n)
            self.recs.append(str(rec))
        self.calls = 0

    def parse_match(self, recs):
        self.calls += 1
        return SAMPLE_MATCH, {"raw": True}, ["a warning"]

    def parse(self):
        return parse_cache.parse(self.recs, self.parse_match, where=self.cache)

    def test_a_match_is_parsed_once_then_loaded_exactly_as_it_was(self):
        first = self.parse()
        again = self.parse()
        self.assertEqual(self.calls, 1)
        self.assertEqual(again, first)
        self.assertEqual(again[0], SAMPLE_MATCH)
        self.assertEqual(again[2], ["a warning"])

    def test_the_order_of_the_files_doesnt_matter(self):
        self.parse()
        parse_cache.parse(self.recs[::-1], self.parse_match, where=self.cache)
        self.assertEqual(self.calls, 1)

    def test_a_changed_replay_is_parsed_again(self):
        self.parse()
        Path(self.recs[1]).write_bytes(b"round 2, longer now")
        self.parse()
        self.assertEqual(self.calls, 2)

    def test_a_new_round_in_the_match_is_parsed_again(self):
        self.parse()
        extra = self.match / "Match-R03.rec"
        extra.write_bytes(b"round 3")
        self.recs.append(str(extra))
        self.parse()
        self.assertEqual(self.calls, 2)

    def test_a_new_parser_reads_every_match_again(self):
        self.parse()
        with mock.patch.object(parse_cache, "parser_fingerprint", return_value="a newer app"):
            self.parse()
        self.assertEqual(self.calls, 2)

    def test_a_damaged_entry_is_parsed_again_and_replaced(self):
        self.parse()
        entry = next(self.cache.glob("*.json.gz"))
        entry.write_bytes(b"not gzip at all")
        self.assertEqual(self.parse()[0], SAMPLE_MATCH)
        self.assertEqual(self.calls, 2)
        entry.write_bytes(gzip.compress(b'{"match": {}}'))  # valid, but not what's stored
        self.parse()
        self.assertEqual(self.calls, 3)

    def test_errors_are_not_kept(self):
        def fails(recs):
            self.calls += 1
            raise ReplayParseError("the game was still writing it")

        for _ in range(2):
            with self.assertRaises(ReplayParseError):
                parse_cache.parse(self.recs, fails, where=self.cache)
        self.assertEqual(self.calls, 2)
        self.assertEqual(list(self.cache.glob("*.json.gz")), [])

    def test_missing_files_are_never_cached(self):
        recs = [str(self.tmp / "gone" / "Match-R01.rec")]
        self.assertIsNone(parse_cache.key(recs))
        parse_cache.parse(recs, self.parse_match, where=self.cache)
        parse_cache.parse(recs, self.parse_match, where=self.cache)
        self.assertEqual(self.calls, 2)

    def test_a_cache_that_cant_be_written_still_parses(self):
        blocked = self.tmp / "a file, not a folder"
        blocked.write_text("x")
        result = parse_cache.parse(self.recs, self.parse_match, where=blocked)
        self.assertEqual(result[0], SAMPLE_MATCH)

    def test_only_the_most_recently_used_are_kept(self):
        self.cache.mkdir()
        now = time.time()
        for i in range(5):
            entry = self.cache / f"{i}.json.gz"
            entry.write_bytes(b"x")
            os.utime(entry, (now - 100 * i, now - 100 * i))  # 0 is the newest
        (self.cache / "half-written.tmp").write_bytes(b"x")
        self.assertEqual(parse_cache.prune(self.cache, keep=3), 3)
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), ["0.json.gz", "1.json.gz", "2.json.gz"])

    def test_loading_a_match_marks_it_recently_used(self):
        self.parse()
        entry = next(self.cache.glob("*.json.gz"))
        os.utime(entry, (1, 1))
        self.parse()
        self.assertGreater(entry.stat().st_mtime, 1)


class TestSources(unittest.TestCase):
    """Only a replay folder on this PC is cached: uploads live in throwaway folders, and the public
    website keeps nothing between visitors."""

    def state(self, kind):
        return {"sig": (kind, "x"), "groups": {"m": ["m-R01.rec"]}, "parsed": {}}

    def test_a_folder_on_this_pc_goes_through_the_cache(self):
        import sources

        with mock.patch.object(sources.parse_cache, "parse", return_value=(SAMPLE_MATCH, {}, [])) as cached, \
                mock.patch.object(sources.app_info, "is_public_host", return_value=False):
            self.assertEqual(sources.parse(self.state("path"), "m")[0], SAMPLE_MATCH)
        cached.assert_called_once()

    def test_uploads_and_the_public_website_are_not_cached(self):
        import sources

        for kind, public in (("upload", False), ("path", True)):
            with mock.patch.object(sources.parse_cache, "parse") as cached, \
                    mock.patch.object(sources, "parse_match", return_value=(SAMPLE_MATCH, {}, [])), \
                    mock.patch.object(sources.app_info, "is_public_host", return_value=public):
                sources.parse(self.state(kind), "m")
            cached.assert_not_called()


if __name__ == "__main__":
    unittest.main()
