"""Tests for the assistant: assistant.py (answers to anything, analysis, follow-ups), assistant_knowledge.py,
question_reports.py, and its panel on every page (assistant_ui.py) and the Ask page's Report button.
Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from streamlit.testing.v1 import AppTest

import assistant_knowledge as kb
import demo_season
import desktop_settings
import parser as replay_parser
import question_reports as qr
import season_stats
from ask_engine import Vocab
from assistant import Conversation, answer_anything, starters
from stats_db import StatsDB
from tab import PAGES

APP = str(Path(__file__).with_name("app.py"))
NOW = datetime(2026, 10, 17, 12, 0)


class TestAnswers(unittest.TestCase):
    """Every kind of question gets an answer, and says when it couldn't really answer."""

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def answer(self, question: str):
        return answer_anything(question, self.db, now=NOW)

    def test_stats_questions_go_to_the_ask_engine(self):
        reply = self.answer("What is my KOST?")
        self.assertEqual((reply.kind, reply.answered), ("stats", True))
        self.assertTrue(reply.text.startswith("Your KOST is"))
        self.assertIsNotNone(reply.answer)
        self.assertTrue(self.answer("my best map").table[1])

    def test_a_stat_explained(self):
        for question in ("What is KOST?", "what does EPS mean", "explain K/D"):
            with self.subTest(question):
                reply = self.answer(question)
                self.assertEqual(reply.kind, "define")
                self.assertTrue(reply.answered)

    def test_how_to_use_the_app(self):
        cases = {"How do I add my matches?": "dashboard", "where are my replays": "download",
                 "how do I export a csv": "dashboard", "how do I build a team": "teams",
                 "how do I change the colors": "theme"}
        for question, page in cases.items():
            with self.subTest(question):
                reply = self.answer(question)
                self.assertEqual(reply.kind, "guide")
                self.assertEqual(reply.links[0][0], page)
                self.assertIn(reply.links[0][0], PAGES)

    def test_a_ranking_isnt_mistaken_for_the_league_page(self):
        self.assertEqual(self.answer("where do I rank in my league").kind, "stats")

    def test_analysis_of_me_another_player_a_team_and_a_match(self):
        mine = self.answer("analyze my stats")
        self.assertEqual(mine.kind, "analysis")
        self.assertIn("Strengths", mine.text)
        self.assertIn("What to work on", mine.text)
        self.assertEqual(mine.columns, ["Stat", "Fabian", "Average"])
        focus = self.answer("what should I work on?")
        self.assertNotIn("**Strengths**", focus.text)  # only what to work on
        self.assertIn("Sacri's analysis", self.answer("analyze Sacri").text)
        team = self.answer("analyze my team")
        self.assertIn("Lakeside Varsity: team analysis", team.text)
        self.assertEqual(team.columns[0], "Player")
        match = self.answer("analyze my last match")
        self.assertIn("Border", match.text)
        self.assertIn("You lost **5–7**", match.text)

    def test_more_stats_than_ask(self):
        self.assertIn("losing streak", self.answer("what's my win streak").text)
        self.assertIn("You last played on", self.answer("when did I last play?").text)
        duo = self.answer("who do I win most with")
        self.assertIn("You win most with", duo.text)
        self.assertEqual(duo.columns, ["Teammate", "Matches", "Record", "Win %"])

    def test_small_talk_and_arithmetic(self):
        self.assertEqual(self.answer("hey").kind, "smalltalk")
        self.assertEqual(self.answer("thanks!").kind, "smalltalk")
        self.assertEqual(self.answer("2 + 2 * 3").text, "**8**")
        self.assertIsNone(answer_anything("__import__('os')", self.db).answer)  # nothing is evaluated

    def test_what_it_cant_answer_is_still_answered_and_can_be_reported(self):
        for question in ("what's the weather like", "asdkjh qwe", "how many teabags do I have"):
            with self.subTest(question):
                reply = self.answer(question)
                self.assertFalse(reply.answered)
                self.assertTrue(reply.text)
                self.assertEqual(len(reply.suggestions), 3)  # questions close to it
        self.assertIn("teabags", self.answer("how many teabags do I have").text)

    def test_a_guide_it_doesnt_have_says_so_and_shows_the_stats(self):
        reply = self.answer("how do I play Bank")
        self.assertFalse(reply.answered)
        self.assertIn("map guides", reply.text)
        self.assertIn("Bank", reply.text)

    def test_with_no_matches_it_says_how_to_start(self):
        with StatsDB(":memory:") as empty:
            reply = answer_anything("analyze my stats", empty)
            self.assertEqual(reply.kind, "guide")
            self.assertIn("no matches in your stats yet", reply.text)
            self.assertEqual(answer_anything("what is KOST", empty).kind, "define")
            self.assertEqual(answer_anything("", empty).kind, "empty")

    def test_this_page(self):
        reply = answer_anything("what does this page do?", self.db, page="history")
        self.assertEqual(reply.links, [("history", "Open Match History")])
        for page in PAGES:
            self.assertTrue(starters(page))


class TestMoreAnswers(unittest.TestCase):
    """Questions the Ask engine alone got wrong or couldn't answer."""

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def text(self, question: str) -> str:
        return answer_anything(question, self.db, now=NOW).text

    def test_a_stat_with_a_filter_is_a_question_not_a_definition(self):
        self.assertTrue(self.text("what is our trade kills when we won").startswith("Your team has"))
        self.assertTrue(self.text("what is the team's rating when we won").startswith("Your team"))

    def test_whose(self):
        self.assertTrue(self.text("everyone's headshot percentage on Bank").startswith("Everyone's"))
        self.assertTrue(self.text("the team's aces").startswith("Your team"))
        self.assertEqual(self.text("how many matches have we won"), "Your team has 6 wins over 12 matches.")
        self.assertTrue(self.text("what's our record").startswith("Your team is 6–6"))

    def test_ban_or_pick_a_map(self):
        self.assertTrue(self.text("which map should we ban").startswith("Ban **Kafe Dostoyevsky**"))
        self.assertTrue(self.text("which map should we pick").startswith("Pick **Bank**"))

    def test_when_i_play_best(self):
        self.assertIn("Evening", self.text("do I play better in the evening"))
        day = answer_anything("which day do I play best", self.db, now=NOW)
        self.assertIn("Thursdays", day.text)
        self.assertEqual(day.columns[0], "When")

    def test_consistency_and_improvement(self):
        self.assertIn("consistent", self.text("how consistent am I"))
        self.assertIn("has improved the most", self.text("who's improving the most"))

    def test_averages_per_match(self):
        self.assertEqual(self.text("my average kills per game"), "You average **8.2 kills** per match (98 over 12 matches).")
        self.assertTrue(self.text("Sacri's average deaths per match").startswith("Sacri averages"))

    def test_a_ranking_where_nobody_has_any(self):
        self.assertEqual(self.text("who has the most aces"), "Nobody has any aces yet.")


class TestNeverCrashes(unittest.TestCase):
    """Random and hostile input never raises, always gets words back, and charts always draw."""

    def test_random_questions(self):
        import random

        from chart_engine import build, parse
        from chart_view import altair_chart
        rng = random.Random(11)
        words = ("my our Sacri Sens BC Lakeside Varsity K/D kills EPS KOST aces clutches win rate best worst top 5 "
                 "most over time per week by map by operator on attack defense Bank Thorn ranked scrims last 3 "
                 "matches since Sep 20 vs compare analyze chart pie heatmap scatter histogram box how what who "
                 "which why ? % 0 -1 999999999999999999999 ; DROP TABLE matches -- <b> 🙂 Jäger").split()
        hostile = ["", "?", "a" * 3000, "\x00", "' OR 1=1 --", "my kills in my last 10000000000000000000000 matches",
                   "last 99999999 weeks", "9" * 400 + "+1", "2**99999", "10/0", "compare me and me", "and?", "why?"]
        with StatsDB(":memory:") as db, StatsDB(":memory:") as empty:
            demo_season.load(db)
            vocab = Vocab.from_db(db)
            chat = Conversation()
            for q in hostile + [" ".join(rng.choice(words) for _ in range(rng.randint(1, 10))) for _ in range(300)]:
                with self.subTest(q=q[:60]):
                    self.assertTrue(answer_anything(q, db, now=NOW).text.strip())
                    self.assertTrue(answer_anything(q, empty, now=NOW).text.strip())
                    self.assertTrue(chat.reply(q, db, now=NOW).text.strip())
                    chart = build(parse(q, vocab, NOW), db, vocab)
                    if chart.ok:
                        altair_chart(chart).to_dict()


class TestConversation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_follow_ups(self):
        chat = Conversation()
        self.assertEqual(chat.reply("my K/D on Bank", self.db, now=NOW).text, "Your K/D is 1.20 over 2 matches on Bank.")
        defense = chat.reply("what about on defense?", self.db, now=NOW)
        self.assertEqual(defense.resolved, "my K/D on Bank on defense")
        self.assertIn("on defense", defense.text)
        other = chat.reply("and Sacri?", self.db, now=NOW)
        self.assertTrue(other.text.startswith("Sacri's K/D"))
        attack = chat.reply("how does he do on attack", self.db, now=NOW)
        self.assertEqual(attack.resolved, "how does Sacri do on attack")
        self.assertIn("Sacri", chat.reply("why?", self.db).text)
        kills = chat.reply("kills instead", self.db, now=NOW)
        self.assertIn("kills", kills.text)
        self.assertIn("Sacri", kills.text)
        self.assertEqual(chat.reply("more", self.db).links, [("ask", "Open it on Ask")])

    def test_a_new_question_isnt_read_as_a_follow_up(self):
        chat = Conversation()
        chat.reply("my K/D on Bank", self.db, now=NOW)
        reply = chat.reply("Who has the most clutches?", self.db, now=NOW)
        self.assertEqual(reply.resolved, "")
        self.assertEqual(chat.reply("Sens", self.db, now=NOW).resolved, "")  # a name alone: how they're doing

    def test_the_memory_is_plain_values(self):
        state = {}
        Conversation(state).reply("analyze Sacri", self.db)
        json.dumps(state)  # the Session can keep it
        self.assertEqual(state["last_player"], "Sacri")


class TestKnowledge(unittest.TestCase):

    def test_every_guide_links_to_a_real_tab(self):
        for topic in kb.GUIDE:
            with self.subTest(topic.key):
                self.assertTrue(topic.page is None or topic.page in PAGES)
                self.assertTrue(topic.words and topic.text)

    def test_keys_are_unique(self):
        keys = [t.key for t in kb.GUIDE + kb.GLOSSARY]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_question_that_isnt_a_definition(self):
        self.assertIsNone(kb.define("my KOST over time"))
        self.assertIsNone(kb.guide("my best map"))


class TestReports(unittest.TestCase):

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_saved_newest_first_and_the_same_question_once(self):
        qr.save_report(self.folder, qr.new_report("How many teabags?", where="Ask"))
        qr.save_report(self.folder, qr.new_report("my clutch rate", where="Assistant", comment="a percentage"))
        qr.save_report(self.folder, qr.new_report("how many TEABAGS?", where="Assistant"))
        reports = qr.load_reports(self.folder)
        self.assertEqual([r["question"] for r in reports], ["how many TEABAGS?", "my clutch rate"])
        qr.delete_report(self.folder, reports[0]["id"])
        self.assertEqual([r["question"] for r in qr.load_reports(self.folder)], ["my clutch rate"])

    def test_a_damaged_file_loses_only_the_damaged_lines(self):
        qr.save_report(self.folder, qr.new_report("one"))
        with open(self.folder / qr.REPORTS_FILE, "a", encoding="utf-8") as file:
            file.write("{not json\n[1, 2]\n")
        self.assertEqual([r["question"] for r in qr.load_reports(self.folder)], ["one"])
        self.assertEqual(qr.load_reports(self.folder / "missing"), [])
        self.assertEqual(qr.load_reports(None), [])

    def test_the_github_issue_is_filled_in(self):
        report = qr.new_report("How many teabags do I have?", answer="I couldn't work that one out.",
                               where="Assistant", page="history", comment="Count them please", version="1.6.5")
        url = urlparse(qr.issue_url(report, "julio208920/r6-dissect"))
        self.assertEqual(url.netloc + url.path, "github.com/julio208920/r6-dissect/issues/new")
        query = parse_qs(url.query)
        self.assertEqual(query["title"], ["Ask couldn't answer: How many teabags do I have?"])
        self.assertEqual(query["labels"], [qr.ISSUE_LABEL])
        body = query["body"][0]
        for text in ("> How many teabags do I have?", "Count them please", "Assistant (on history)", "1.6.5"):
            self.assertIn(text, body)

    def test_long_reports_are_cut_short(self):
        report = qr.new_report("x" * 5000, comment="y" * 5000)
        self.assertEqual(len(report["question"]), 1000)
        self.assertLess(len(qr.issue_url(report, "o/r")), 9000)
        self.assertTrue(qr.issue_title(report).endswith("…"))


class TestSettings(unittest.TestCase):

    def test_settings_are_kept_side_by_side(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(desktop_settings.setting(folder, "assistant"))
            desktop_settings.save_setting(folder, "assistant", False)
            desktop_settings.save_launch_chime(folder, False)
            self.assertFalse(desktop_settings.setting(folder, "assistant"))
            self.assertFalse(desktop_settings.launch_chime_enabled(folder))
            desktop_settings.save_setting(folder, "assistant", True)
            self.assertTrue(desktop_settings.setting(folder, "assistant"))
            self.assertFalse(desktop_settings.launch_chime_enabled(folder))


class TestPanel(unittest.TestCase):
    """The panel on every page, with the demo season."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.data = Path(folder) / "R6MatchStats"
        for patch in (mock.patch.object(season_stats, "DEFAULT_DB_PATH", Path(folder) / "stats.db"),
                      mock.patch.object(replay_parser, "find_replay_folders", return_value=[]),
                      mock.patch("rosters.update_on_launch"),
                      mock.patch.dict(os.environ, {"LOCALAPPDATA": folder})):
            patch.start()
            self.addCleanup(patch.stop)

    def start(self) -> AppTest:
        at = AppTest.from_file(APP, default_timeout=60)
        at.query_params["demo"] = "1"
        return at.run()

    def chat(self, at: AppTest) -> list[dict]:
        return at.session_state["assistant_chat"] if "assistant_chat" in at.session_state else []

    def test_ask_it_change_tabs_and_the_conversation_stays(self):
        at = self.start()
        self.assertFalse(at.exception)
        at.button(key="assistant_launch").click().run()
        self.assertTrue(at.session_state["assistant_open"])
        at.chat_input(key="assistant_input").set_value("analyze my stats").run()
        self.assertFalse(at.exception)
        chat = self.chat(at)
        self.assertEqual([m["role"] for m in chat], ["user", "assistant"])
        self.assertEqual(chat[1]["kind"], "analysis")
        at.chat_input(key="assistant_input").set_value("what about on defense?").run()
        at.switch_page("history.py").run()
        self.assertFalse(at.exception)
        self.assertEqual(len(self.chat(at)), 4)
        self.assertTrue(at.session_state["assistant_open"])  # still open on the new tab
        self.assertTrue(any(b.key == "assistant_close" for b in at.button))

    def test_a_starter_question_and_clearing(self):
        at = self.start()
        at.button(key="assistant_launch").click().run()
        at.button(key="assistant_start_0").click().run()
        self.assertEqual(self.chat(at)[0]["text"], starters("dashboard")[0])
        at.button(key="assistant_clear").click().run()
        self.assertEqual(self.chat(at), [])

    def test_report_from_the_chat(self):
        at = self.start()
        at.button(key="assistant_launch").click().run()
        at.chat_input(key="assistant_input").set_value("what's the weather like").run()
        at.button(key="assistant_report_1_button").click().run()
        at.text_area(key="assistant_report_1_comment").input("Not about R6, fine")
        at.button(key="FormSubmitter:assistant_report_1_form-Save report").click().run()
        self.assertFalse(at.exception)
        [report] = qr.load_reports(self.data)
        self.assertEqual((report["question"], report["where"], report["comment"]),
                         ("what's the weather like", "Assistant", "Not about R6, fine"))

    def test_turned_off_it_is_gone_everywhere(self):
        at = self.start()
        at.toggle(key="assistant_toggle").set_value(False).run()
        self.assertFalse([b for b in at.button if b.key == "assistant_launch"])
        at.switch_page("ask.py").run()
        self.assertFalse([b for b in at.button if b.key == "assistant_launch"])

    def test_the_windows_app_remembers_it_off(self):
        with mock.patch.dict(os.environ, {"R6_DESKTOP": "1"}):
            at = self.start()
            at.toggle(key="assistant_toggle").set_value(False).run()
        self.assertFalse(desktop_settings.setting(self.data, "assistant"))

    def test_the_ask_page_answers_anything_and_reports(self):
        at = self.start()
        at.switch_page("ask.py").run()
        at.text_input(key="ask_question").set_value("how many teabags do I have")
        at.button(key="FormSubmitter:ask_form-Ask").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("teabags" in i.value for i in at.info))
        at.button(key="ask_report_button").click().run()
        at.button(key="FormSubmitter:ask_report_form-Save report").click().run()
        self.assertEqual(qr.load_reports(self.data)[0]["question"], "how many teabags do I have")
        self.assertTrue(any("Reported questions" in e.label for e in at.expander))
        at.text_input(key="ask_question").set_value("analyze my team")
        at.button(key="FormSubmitter:ask_form-Ask").click().run()
        self.assertTrue(any("team analysis" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()
