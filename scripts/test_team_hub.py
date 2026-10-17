"""Two independent browser sessions must share only their authenticated team's data."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from sample_data import SAMPLE_MATCH
from stats_db import StatsDB
from team_accounts import TeamStore

APP = str(Path(__file__).with_name('app.py'))


class TeamHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patch in (mock.patch.dict(os.environ, {'R6_TEAM_DATA_DIR': self.temp.name}),
                      mock.patch('parser.find_replay_folders', return_value=[]),
                      mock.patch('rosters.update_on_launch')):
            patch.start()
            self.addCleanup(patch.stop)
        self.store = TeamStore(self.temp.name)
        self.store.create('coach@school.edu', 'shared long password', 'School Team')
        self.token = self.store.login('coach@school.edu', 'shared long password')

    def app(self, token=None):
        app = AppTest.from_file(APP, default_timeout=30)
        if token:
            app.session_state['team_token'] = token
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_sign_in_and_sign_out_clear_the_old_identity(self):
        app = self.app().switch_page('team_hub.py').run()
        next(i for i in app.text_input if i.label == 'School email').set_value('coach@school.edu')
        next(i for i in app.text_input if i.label == 'Team password').set_value('shared long password')
        next(b for b in app.button if b.label == 'Sign in to team').click().run()
        self.assertFalse(app.exception)
        token = app.session_state['team_token']
        self.assertEqual(self.store.account(token)['name'], 'School Team')
        app.switch_page('team_hub.py').run()
        app.session_state['team_person'] = 'old-person'
        next(b for b in app.button if b.label == 'Sign out of this device').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('team_token', app.session_state)
        self.assertNotIn('team_person', app.session_state)

    def test_shared_stats_and_player_identity_do_not_change_match_recorder(self):
        people = [{'id': 'a'*32, 'name': 'Selected Player', 'player': 'Kanto', 'roles': ['Player', 'Captain']}]
        self.store.save_directory(self.token, people, 0)
        path = self.store.team_folder(self.token) / 'stats.db'
        with StatsDB(path) as db:
            db.import_match('team-match', SAMPLE_MATCH)
            recorder = db.me()
        first = self.app(self.token).switch_page('team_hub.py').run()
        next(s for s in first.selectbox if s.label == 'Who are you viewing as?').select('a'*32).run()
        self.assertFalse(first.exception)
        first.switch_page('history.py').run()
        self.assertFalse(first.exception)
        first.switch_page('operators.py').run()
        self.assertFalse(first.exception)
        self.assertEqual(next(s for s in first.selectbox if s.label == 'Player').value, 'kanto')
        # A second session reads the same DB; its identity choice remains independent.
        second_token = self.store.login('coach@school.edu', 'shared long password')
        second = self.app(second_token).switch_page('team_hub.py').run()
        self.assertTrue(any('School Team' in s.value for s in second.subheader))
        self.assertEqual(second.session_state['team_person'], '')
        with StatsDB(path) as db:
            self.assertEqual(db.summary()['matches'], 1)
            self.assertEqual(db.me(), recorder)

    def test_profile_roles_save_and_other_team_cannot_see_them(self):
        app = self.app(self.token).switch_page('team_hub.py').run()
        next(i for i in app.text_input if i.label == 'Display name').set_value('Head Coach')
        next(i for i in app.multiselect if i.label == 'Team roles').set_value(['Coach', 'Analyst'])
        next(b for b in app.button if b.label == 'Save member').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.store.account(self.token)['directory'][0]['roles'], ['Coach', 'Analyst'])
        self.store.create('coach@other.edu', 'other long password', 'Other Team')
        token = self.store.login('coach@other.edu', 'other long password')
        other = self.app(token).switch_page('team_hub.py').run()
        self.assertFalse(other.dataframe)
        with StatsDB(self.store.team_folder(token) / 'stats.db') as db:
            self.assertEqual(db.summary()['matches'], 0)

    def test_documents_detect_concurrent_changes(self):
        self.store.save_document(self.token, 'planning', {'notes': 'new'}, 0)
        from team_accounts import AccountError
        with self.assertRaises(AccountError):
            self.store.save_document(self.token, 'planning', {'notes': 'stale'}, 0)
        self.assertEqual(self.store.document(self.token, 'planning'), ({'notes': 'new'}, 1))

    def test_recovery_revokes_a_live_app_session(self):
        app = self.app(self.token)
        self.store.logout(self.token)
        app.run()
        self.assertFalse(app.exception)
        self.assertNotIn('team_token', app.session_state)
        self.assertTrue(any('session ended' in w.value for w in app.warning))

    def test_demo_does_not_write_shared_theme_or_planning(self):
        self.store.save_document(self.token, 'theme', {'name': 'Real Team'}, 0)
        app = self.app(self.token)
        next(b for b in app.button if b.label == 'Try the demo season').click().run()
        self.assertFalse(app.exception)
        app.switch_page('team_hub.py').run()
        self.assertTrue(any('Leave the demo' in w.value for w in app.warning))
        self.assertEqual(self.store.document(self.token, 'theme')[0]['name'], 'Real Team')
        self.assertEqual(self.store.document(self.token, 'planning'), ({}, 0))

    def test_account_creation_and_recovery_code_dismissal(self):
        app = self.app().switch_page('team_hub.py').run()
        values = {'Team name': 'New School Team', 'School email ending in .edu': 'new@school.edu',
                  'Choose a shared password': 'new shared password', 'Repeat shared password': 'new shared password'}
        for item in app.text_input:
            if item.label in values:
                item.set_value(values[item.label])
        next(b for b in app.button if b.label == 'Create team account').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.session_state['team_new_recovery'])
        self.assertNotIn('team_token', app.session_state)
        next(b for b in app.button if b.label == 'I saved the recovery code').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('team_new_recovery', app.session_state)
        self.assertEqual(next(i for i in app.text_input if i.label == 'Choose a shared password').value, '')
        self.assertTrue(self.store.login('new@school.edu', 'new shared password'))

    def test_password_recovery_form_rotates_code_and_revokes_previous_login(self):
        original = self.store.create('recover@school.edu', 'old shared password', 'Recover Team')
        token = self.store.login('recover@school.edu', 'old shared password')
        app = self.app().switch_page('team_hub.py').run()
        values = {'Account school email': 'recover@school.edu', 'Saved recovery code': original,
                  'New shared password': 'new shared password', 'Repeat new password': 'new shared password'}
        for item in app.text_input:
            if item.label in values:
                item.set_value(values[item.label])
        next(b for b in app.button if b.label == 'Reset team password').click().run()
        self.assertFalse(app.exception)
        self.assertNotEqual(app.session_state['team_new_recovery'], original)
        from team_accounts import AccountError
        with self.assertRaises(AccountError):
            self.store.account(token)
        self.assertTrue(self.store.login('recover@school.edu', 'new shared password'))
