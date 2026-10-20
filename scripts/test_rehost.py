import copy
import unittest

import rosters
from rehost import combine
from sample_data import SAMPLE_MATCH, TEAM0
from stats_db import StatsDB, NotAdded


class RehostTests(unittest.TestCase):
    def parts(self):
        first, second = copy.deepcopy(SAMPLE_MATCH), copy.deepcopy(SAMPLE_MATCH)
        first['match_id'], second['match_id'] = 'before-rehost', 'after-rehost'
        first['rounds'], second['rounds'] = first['rounds'][:2], second['rounds'][2:]
        return first, second

    def test_all_rounds_count_once_and_four_maps_stay_four(self):
        first, second = self.parts()
        with StatsDB(':memory:') as db:
            db.import_match('first', first, players=TEAM0[:4])
            db.import_match('second', second, players=TEAM0[:4])
            for i in range(3):
                m = dict(SAMPLE_MATCH, match_id=f'other-{i}')
                db.import_match(f'other-{i}', m, players=TEAM0)
            db.import_rehost('second', [first, second], players=TEAM0)
            self.assertEqual(db.summary()['matches'], 4)
            rows = db.query('SELECT player,COUNT(*) AS n FROM match_players GROUP BY player')
            self.assertTrue(all(r['n'] == 4 for r in rows))
            self.assertEqual(db.query("SELECT rounds FROM matches WHERE match_id='after-rehost'")[0]['rounds'], len(SAMPLE_MATCH['rounds']))
            self.assertEqual(len(db.saved_rehost('before-rehost')['match']['rounds']), len(SAMPLE_MATCH['rounds']))
            db.import_rehost('second', [first, second], players=TEAM0)
            self.assertEqual(db.summary()['matches'], 4)
            with self.assertRaises(NotAdded):
                db.import_match('first', first, players=TEAM0)
            with self.assertRaises(NotAdded):
                db.import_match('second', second, players=TEAM0)
            db.remove_match('after-rehost')
            db.import_match('first', first, players=TEAM0)
            self.assertEqual(db.summary()['matches'], 4)

    def test_mismatched_map_is_rejected(self):
        first, second = self.parts()
        second['map'] = 'Different Map'
        with self.assertRaises(ValueError):
            combine([first, second])

    def test_team_swap_is_aligned(self):
        first, second = self.parts()
        for p in second['players']:
            p['team'] = 1-p['team']
        for r in second['rounds']:
            r['winner_team'] = 1-r['winner_team']
            r['attack_team'] = 1-r['attack_team']
        merged = combine([first, second])
        self.assertEqual([r['winner_team'] for r in merged['rounds']], [r['winner_team'] for r in SAMPLE_MATCH['rounds']])

    def test_missing_roster_player_is_offered_when_reopening(self):
        names = ['Talus.CSP','Smile.CSP','Other.CSP','Stranger']
        roster = rosters.roster_names(names[:3])
        self.assertEqual(rosters.default_selection(names, roster, ['TALUS.CSP']), names[:3])
        self.assertEqual(rosters.default_selection(names, {}, ['TALUS.CSP']), ['Talus.CSP'])


if __name__ == '__main__':
    unittest.main()
