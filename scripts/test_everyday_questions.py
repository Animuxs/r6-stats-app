"""Everyday questions that got the wrong answer when asked about real replays: each test is one of them."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import demo_season
from ask_engine import Vocab, ask, interpret
from assistant import Conversation, answer_anything
from stats_db import StatsDB, nice_time


class TestEverydayQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.vocab = Vocab.from_db(cls.db)
        cls.mine = [m for m in cls.db.match_list(cls.vocab.me) if m.get("player")]  # newest first

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def answer(self, question):
        return answer_anything(question, self.db).text

    def test_when_did_we_last_play_a_map(self):  # was: the last match, on any map
        newest = self.mine[0]
        other = next(m for m in self.mine if m["map"] != newest["map"])
        reply = self.answer(f"when did we last play {other['map']}?")
        self.assertIn(nice_time(other["played_at"]), reply)
        self.assertIn(f"on {other['map']}", reply)

    def test_what_map_did_we_play_last(self):  # was: your best map
        self.assertIn(f"on {self.mine[0]['map']}", self.answer("what map did we play last?"))

    def test_show_my_losses_lists_them(self):  # was: how many
        answer = ask("show my losses", self.db, vocab=self.vocab)
        losses = [m for m in self.mine if m["won"] == 0]
        self.assertEqual(answer.query.kind, "matches")
        self.assertEqual(len(answer.rows), len(losses))
        self.assertTrue(answer.headline.startswith(f"Your {len(losses)} loss"), answer.headline)
        self.assertEqual(ask("how many losses do I have", self.db, vocab=self.vocab).query.kind, "count")

    def test_how_many_headshots_is_a_count(self):  # was: the headshot rate
        answer = ask("how many headshots did I get?", self.db, vocab=self.vocab)
        self.assertEqual(answer.query.metrics, ["headshots"])
        total = self.db.query("SELECT SUM(headshots) AS n FROM match_players WHERE player_key = ?",
                              [self.vocab.me])[0]["n"]
        self.assertEqual(answer.rows[0]["Headshots"], total)
        self.assertEqual(ask("my headshot rate", self.db, vocab=self.vocab).query.metrics, ["hs"])

    def test_the_ace_is_a_five_kill_round(self):  # was: the operator Ace
        for question in ("who got the ace?", "did anyone get an ace?"):
            query = ask(question, self.db, vocab=self.vocab).query
            self.assertEqual((query.metrics, query.operators), (["aces"], []), question)
        self.assertEqual(ask("my K/D as Ace", self.db, vocab=self.vocab).query.operators, ["Ace"])

    def test_what_operators_does_someone_play(self):  # was: their best operator by win rate
        self.assertIn("most played operator", self.answer("what operators do I play?"))

    def test_last_night_and_tonight(self):  # were: the time of day ("plays best in the Late night")
        played = datetime.strptime(self.mine[0]["played_at"][:19], "%Y-%m-%d %H:%M:%S")
        morning_after = played.replace(hour=10) + timedelta(days=1)
        query = interpret("my K/D last night", self.vocab, morning_after)
        self.assertLessEqual(query.since, self.mine[0]["played_at"])
        self.assertGreater(query.until, self.mine[0]["played_at"])
        # past midnight, tonight is still the evening that began yesterday
        after_midnight = played.replace(hour=1) + timedelta(days=1)
        self.assertLessEqual(interpret("my K/D tonight", self.vocab, after_midnight).since, self.mine[0]["played_at"])
        mate = self.db.query("SELECT player FROM match_players WHERE match_id = ? AND team = ? AND player_key != ?",
                             [self.mine[0]["match_id"], self.mine[0]["team"], self.vocab.me])[0]["player"]
        reply = answer_anything(f"how did {mate} do last night?", self.db, now=morning_after).text
        self.assertTrue(reply.startswith(mate), reply)  # their numbers, not the session's recap

    def test_when_do_i_play_best_or_worst(self):  # were: Match day's schedule; and "best" for "worst"
        self.assertIn("play best in the", self.answer("what time do I play best?"))
        self.assertIn("Match day", self.answer("when do we play next?"))
        db = StatsDB(":memory:")
        demo_season.load(db)
        late = [m["match_id"] for m in self.mine[::2]]  # half the matches late at night, so there's a worst time
        db._conn.execute(f"UPDATE matches SET played_at = substr(played_at, 1, 11) || '23:30:00' "
                        f"WHERE match_id IN ({', '.join('?' * len(late))})", late)
        best, worst = (answer_anything(f"when do I play {w}?", db).text for w in ("best", "worst"))
        self.assertIn("play worst in the", worst)
        self.assertNotEqual(best.split("**")[1], worst.split("**")[1])
        db.close()

    def test_where_do_i_is_a_map(self):  # was: a total over every map
        for question in ("where do I die the most?", "where do we lose the most rounds?", "where do I get the most kills?"):
            self.assertEqual(ask(question, self.db, vocab=self.vocab).query.group, "map", question)
        self.assertIn("best map by win rate", ask("where do we win the most?", self.db, vocab=self.vocab).headline)

    def test_most_kills_in_a_round(self):  # was: every kill, added up
        mine = self.db.query("SELECT MAX(kills) AS n FROM round_players WHERE player_key = ?", [self.vocab.me])[0]["n"]
        self.assertTrue(self.answer("my most kills in a round").startswith(f"Your most kills in a round: **{mine}**"))
        anyone = self.db.query("SELECT MAX(kills) AS n FROM round_players")[0]["n"]
        self.assertIn(f"**{anyone}**", self.answer("who had the most kills in a round?"))
        self.assertNotIn("in a round:", self.answer("which map has my best round win rate?"))

    def test_best_round_on_a_map_as_an_operator_in_a_month(self):
        best = self.db.query("SELECT r.kills, r.operator, m.map, m.played_at FROM round_players r "
                             "JOIN matches m ON m.match_id = r.match_id WHERE r.player_key = ? AND r.operator != '' "
                             "ORDER BY r.kills DESC LIMIT 1", [self.vocab.me])[0]
        month = datetime.strptime(best["played_at"][:10], "%Y-%m-%d").strftime("%B %Y")
        reply = self.answer(f"my best round on {best['map']} as {best['operator']} in {month}")
        self.assertTrue(reply.startswith(f"Your most kills in a round: **{best['kills']}**"), reply)
        self.assertIn(f"on {best['map']} as {best['operator']}", reply)


class TestFollowUps(unittest.TestCase):
    """Follow-ups that lost the thread when asked about real replays."""

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.vocab = Vocab.from_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def chat(self, *questions):
        chat = Conversation()
        return [chat.reply(q, self.db) for q in questions]

    def test_the_other_end_of_a_ranking(self):  # was: "my map my worst", a summary
        _, worst = self.chat("my best map", "and my worst?")
        self.assertEqual(worst.resolved, "my worst map")
        self.assertIn("worst map", worst.text)

    def test_it_is_the_map_or_operator_just_named(self):  # was: "it" ignored
        best, on_it = self.chat("what's my best map?", "how do I do on it?")
        top = best.answer.rows[0]["Map"]
        self.assertEqual(on_it.resolved, f"how do I do on {top}?")
        played, as_it = self.chat("my most played operator", "my K/D as it")
        self.assertEqual(as_it.resolved, f"my K/D as {played.answer.rows[0]['Operator']}")

    def test_a_question_about_the_app_isnt_a_follow_up(self):  # was: glued onto the last stat question
        _, safe = self.chat("my K/D on Bank", "is it safe?")
        self.assertFalse(safe.resolved)
        self.assertNotIn("K/D", safe.text)


if __name__ == "__main__":
    unittest.main()
