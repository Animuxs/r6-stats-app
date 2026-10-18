"""Tests for charts: chart_engine.py (requests read into charts, and their numbers), chart_view.py (every kind
draws), the Charts tab, and charts from the assistant and Ask. Run from the repo root:
python -m unittest discover -s scripts"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

import demo_season
import parser as replay_parser
import season_stats
from ask_engine import Vocab, ask
from assistant import answer_anything
from chart_engine import AXES, CHART_METRICS, KINDS, SPLITS, ChartSpec, build, describe, parse
from chart_view import altair_chart
from stats_db import StatsDB

APP = str(Path(__file__).with_name("app.py"))


class TestParse(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.vocab = Vocab.from_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def parse(self, text: str) -> ChartSpec:
        return parse(text, self.vocab)

    def test_what_each_request_draws(self):
        cases = {  # request: (kind, metric, x, split, who)
            "my K/D over time vs Sacri": ("line", "kd", "match", "who", "players"),
            "pie chart of the operators I play": ("pie", "rounds", "operator", "none", "me"),
            "heatmap of win rate by map and side": ("heatmap", "win_rate", "map", "side", "me"),
            "scatter K/D vs KOST for every player": ("scatter", "kd", "player", "none", "everyone"),
            "bar chart of win rate by map": ("bar", "win_rate", "map", "none", "me"),
            "headshot rate per week": ("line", "hs", "week", "none", "me"),
            "horizontal bar of top 5 players by EPS": ("hbar", "eps", "player", "none", "everyone"),
            "stacked bar of rounds won by map and side": ("stacked", "wins", "map", "side", "me"),
            "box plot of kills by side": ("box", "kills", "match", "side", "me"),
            "kills histogram": ("histogram", "kills", "match", "none", "me"),
            "Lakeside Varsity win rate by month": ("line", "win_rate", "month", "none", "team"),
            "graph": ("line", "kd", "match", "none", "me"),
        }
        for text, expected in cases.items():
            with self.subTest(text):
                s = self.parse(text)
                self.assertEqual((s.kind, s.metric, s.x, s.split, s.who), expected)

    def test_smoothing_running_totals_and_limits(self):
        self.assertEqual(self.parse("rolling average of my EPS").rolling, 5)
        self.assertEqual(self.parse("moving average over 3 of my kills").rolling, 3)
        running = self.parse("running total of my kills")
        self.assertTrue(running.cumulative)
        self.assertEqual(running.rolling, 0)  # "running total" isn't a running average
        self.assertEqual(self.parse("top 3 operators by K/D").limit, 3)

    def test_filters_come_from_the_ask_engine(self):
        s = self.parse("my kills per match on attack on Bank in my last 6 matches")
        self.assertEqual((s.side, s.maps, s.last_matches), ("attack", ["Bank"], 6))
        self.assertIn("on Bank", s.title(self.vocab))
        self.assertIn("on attack", s.title(self.vocab))

    def test_a_pie_of_a_rate_becomes_a_share_of_rounds(self):
        s = self.parse("pie of my K/D by map")
        self.assertEqual(s.metric, "rounds")
        self.assertTrue(s.notes)


class TestBuild(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.vocab = Vocab.from_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def chart(self, text: str):
        return build(parse(text, self.vocab), self.db, self.vocab)

    def test_a_line_per_player_agrees_with_ask(self):
        chart = self.chart("my K/D over time vs Sacri")
        series = defaultdict(list)
        for r in chart.rows:
            series[r["series"]].append(r["y"])
        self.assertEqual(set(series), {"Fabian", "Sacri"})
        self.assertEqual(len(series["Fabian"]), 12)
        trend = ask("my K/D per match", self.db, vocab=self.vocab)
        self.assertEqual(series["Fabian"], [row["K/D"] for row in trend.rows])

    def test_bars_agree_with_ask(self):
        chart = self.chart("bar chart of my win rate by map")
        answer = ask("my win rate by map", self.db, vocab=self.vocab)
        mine = {r["x"]: r["y"] for r in chart.rows}
        for row in answer.rows:
            self.assertEqual(mine[row["Map"]], row["Win %"])

    def test_a_split_by_side_really_splits(self):
        chart = self.chart("box plot of kills by side")
        by_side = defaultdict(list)
        for r in chart.rows:
            by_side[r["x"]].append(r["y"])
        self.assertEqual(set(by_side), {"Attack", "Defense"})
        self.assertNotEqual(sorted(by_side["Attack"]), sorted(by_side["Defense"]))
        bars = self.chart("my kills by map split by side as a stacked bar")
        self.assertEqual({r["series"] for r in bars.rows}, {"Attack", "Defense"})

    def test_running_total_and_smoothing(self):
        total = self.chart("running total of my kills")
        values = [r["y"] for r in total.rows]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], ask("my kills", self.db, vocab=self.vocab).rows[0]["Kills"])
        raw = [r["y"] for r in self.chart("my EPS over time").rows]
        smooth = [r["y"] for r in self.chart("rolling average of 3 of my EPS").rows]
        self.assertEqual(smooth[0], raw[0])
        self.assertAlmostEqual(smooth[2], round(sum(raw[:3]) / 3), delta=1)

    def test_heatmap_and_scatter(self):
        heat = self.chart("heatmap of win rate by map and side")
        self.assertEqual({r["x2"] for r in heat.rows}, {"Attack", "Defense"})
        scatter = self.chart("scatter K/D vs KOST for every player")
        self.assertEqual(len(scatter.rows), 5)
        self.assertTrue(all({"x", "y", "series", "size"} <= set(r) for r in scatter.rows))

    def test_eps_split_by_operator_is_k_d_with_a_note(self):
        chart = self.chart("my EPS by operator")
        self.assertEqual(chart.spec.metric, "kd")
        self.assertTrue(any("EPS" in n for n in chart.notes))

    def test_nothing_to_chart(self):
        spec = ChartSpec(kind="bar", metric="kd", x="map", who="players", players=["nobody"])
        chart = build(spec, self.db, self.vocab)
        self.assertFalse(chart.ok)
        self.assertTrue(chart.notes)
        self.assertEqual(describe(chart), "There's nothing to chart for that.")
        self.assertFalse(build(ChartSpec(who="team", teams=["Not a team"]), self.db, self.vocab).ok)

    def test_every_combination_builds_and_draws(self):
        """Every kind, with every stat and grouping it can take, builds without an error, and draws."""
        for kind in KINDS:
            for metric in CHART_METRICS:
                for x in (AXES if kind != "scatter" else ("player", "operator", "map")):
                    split = "side" if kind == "heatmap" else "none"
                    spec = ChartSpec(kind=kind, metric=metric, metric2="kost", x=x, split=split)
                    with self.subTest(kind=kind, metric=metric, x=x):
                        chart = build(spec, self.db, self.vocab)
                        if chart.ok:
                            altair_chart(chart).to_dict()
        for split in SPLITS:
            with self.subTest(split=split):
                chart = build(ChartSpec(kind="line", split=split), self.db, self.vocab)
                altair_chart(chart).to_dict()

    def test_the_table_has_readable_columns(self):
        table = self.chart("my K/D over time vs Sacri").table()
        self.assertEqual(set(table[0]), {"Match date", "K/D", "Series"})


class TestFromTheAssistant(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_asking_for_a_chart_draws_one(self):
        for text in ("chart my K/D over time", "pie of the operators I play", "plot Sacri vs Sens kills per match",
                     "show me a heatmap of win rate by map and side"):
            with self.subTest(text):
                reply = answer_anything(text, self.db)
                self.assertEqual(reply.kind, "chart")
                self.assertTrue(reply.chart.ok)
        self.assertEqual(answer_anything("how do charts work?", self.db).kind, "guide")


class TestChartsTab(unittest.TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for patch in (mock.patch.object(season_stats, "DEFAULT_DB_PATH", Path(folder) / "stats.db"),
                      mock.patch.object(replay_parser, "find_replay_folders", return_value=[]),
                      mock.patch("rosters.update_on_launch"),
                      mock.patch.dict(os.environ, {"LOCALAPPDATA": folder})):
            patch.start()
            self.addCleanup(patch.stop)
        self.at = AppTest.from_file(APP, default_timeout=60)
        self.at.query_params["demo"] = "1"
        self.at.run()
        self.at.switch_page("charts.py").run()

    def test_a_request_fills_in_the_menus(self):
        at = self.at
        self.assertFalse(at.exception)
        at.text_input(key="chart_prompt").set_value("heatmap of win rate by map and side")
        at.button(key="FormSubmitter:chart_form-Draw").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key="chart_kind").value, KINDS["heatmap"])
        self.assertEqual(at.selectbox(key="chart_x").value, AXES["map"])
        self.assertTrue(at.get("vega_lite_chart"))

    def test_the_menus_change_the_chart(self):
        at = self.at
        at.selectbox(key="chart_kind").set_value(KINDS["pie"]).run()
        at.selectbox(key="chart_x").set_value(AXES["operator"]).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Biggest share" in c.value for c in at.caption))

    def test_examples_keep_and_restore(self):
        at = self.at
        at.button(key="chart_example_3").click().run()  # the scatter
        self.assertEqual(at.selectbox(key="chart_kind").value, KINDS["scatter"])
        at.button(key="chart_keep").click().run()
        at.button(key="chart_example_0").click().run()
        self.assertEqual(at.selectbox(key="chart_kind").value, KINDS["line"])
        at.button(key="chart_saved_0").click().run()
        self.assertEqual(at.selectbox(key="chart_kind").value, KINDS["scatter"])
        at.button(key="chart_forget_0").click().run()
        self.assertFalse([b for b in at.button if b.key == "chart_saved_0"])

    def test_from_ask_to_charts(self):
        at = self.at
        at.switch_page("ask.py").run()
        at.text_input(key="ask_question").set_value("pie of the operators I play")
        at.button(key="FormSubmitter:ask_form-Ask").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(at.get("vega_lite_chart"))
        at.button(key="ask_to_charts").click().run()
        self.assertFalse(at.exception)
        at.switch_page("charts.py").run()  # AppTest doesn't follow st.switch_page; the browser check does
        self.assertEqual(at.selectbox(key="chart_kind").value, KINDS["pie"])


if __name__ == "__main__":
    unittest.main()
