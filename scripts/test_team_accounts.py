"""Account isolation, credential lifecycle and shared-login role semantics."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from team_accounts import AccountError, TeamStore, directory, school_email
from desktop_settings import launch_chime_enabled, save_launch_chime


class TeamAccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TeamStore(self.temp.name)

    def create(self, email='coach@school.edu'):
        recovery = self.store.create(email, 'correct horse battery staple', 'School Varsity')
        token = self.store.login(email, 'correct horse battery staple')
        return token, recovery

    def test_school_format_is_not_a_substring_check_or_ownership_claim(self):
        self.assertEqual(school_email(' Coach@School.EDU '), 'coach@school.edu')
        for value in ('coach@school.edu.evil.com', 'coach@school.education', 'coach@.edu',
                      'coach@school.edu/path', 'coach school.edu', 'a@-school.edu', 'a\n@school.edu'):
            with self.subTest(value=value), self.assertRaises(AccountError):
                school_email(value)

    def test_credentials_are_hashed_and_sessions_can_be_revoked(self):
        token, recovery = self.create()
        self.assertEqual(self.store.account(token)['email'], 'coach@school.edu')
        with sqlite3.connect(Path(self.temp.name) / 'accounts.db') as db:
            dump = '\n'.join(db.iterdump())
        db.close()
        for secret in (token, recovery, 'correct horse battery staple'):
            self.assertNotIn(secret, dump)
        self.store.logout(token)
        with self.assertRaises(AccountError):
            self.store.account(token)

    def test_expired_session_and_forged_tokens_fail(self):
        token, _ = self.create()
        with mock.patch('team_accounts.time.time', return_value=10**12):
            with self.assertRaises(AccountError):
                self.store.account(token)
        with self.assertRaises(AccountError):
            self.store.team_folder('../other-account')

    def test_teams_have_separate_folders_and_directory_updates_detect_conflicts(self):
        first, _ = self.create()
        second, _ = self.create('analyst@other.edu')
        self.assertNotEqual(self.store.team_folder(first), self.store.team_folder(second))
        person = {'id': 'a'*32, 'name': 'Player One', 'player': 'Gamertag', 'roles': ['Player', 'Captain']}
        self.store.save_directory(first, [person], 0)
        self.assertEqual(self.store.account(first)['directory'][0]['roles'], ['Player', 'Captain'])
        self.assertEqual(self.store.account(second)['directory'], [])
        with self.assertRaises(AccountError):
            self.store.save_directory(first, [], 0)

    def test_recovery_rotates_code_and_password_and_invalidates_all_sessions(self):
        first, recovery = self.create()
        second = self.store.login('coach@school.edu', 'correct horse battery staple')
        replacement = self.store.reset_password('coach@school.edu', recovery, 'a new long password')
        for token in (first, second):
            with self.assertRaises(AccountError):
                self.store.account(token)
        with self.assertRaises(AccountError):
            self.store.login('coach@school.edu', 'correct horse battery staple')
        with self.assertRaises(AccountError):
            self.store.reset_password('coach@school.edu', recovery, 'another long password')
        self.assertNotEqual(recovery, replacement)
        self.assertTrue(self.store.account(self.store.login('coach@school.edu', 'a new long password')))

    def test_failed_attempts_are_committed_and_rate_limited(self):
        # PBKDF2 behavior is covered above; keep the repeated lockout check inexpensive.
        with mock.patch('team_accounts._hash', return_value='invalid'):
            for _ in range(10):
                with self.assertRaisesRegex(AccountError, 'Invalid email'):
                    self.store.login('missing@school.edu', 'bad')
            with self.assertRaisesRegex(AccountError, 'Too many attempts'):
                self.store.login('missing@school.edu', 'bad')

    def test_profiles_validate_roles_ids_and_duplicate_usernames(self):
        person = {'id': 'a'*32, 'name': 'Coach', 'player': '', 'roles': ['Other'], 'custom_role': 'Manager'}
        self.assertEqual(directory([person])[0]['custom_role'], 'Manager')
        for changes in ({'roles': ['Administrator']}, {'id': '../bad'}, {'custom_role': ''}, {'name': ''}):
            with self.assertRaises(AccountError):
                directory([{**person, **changes}])
        with self.assertRaises(AccountError):
            directory([person, person])
        with self.assertRaises(AccountError):
            directory([{**person, 'player': 'NAME'}, {**person, 'id': 'b'*32, 'player': 'name'}])

    def test_desktop_chime_preferences_survive_restart_and_keep_other_settings(self):
        folder = Path(self.temp.name)
        self.assertTrue(launch_chime_enabled(folder))
        (folder / 'desktop-settings.json').write_text(json.dumps({'other': 'kept'}))
        save_launch_chime(folder, False)
        self.assertFalse(launch_chime_enabled(folder))
        self.assertEqual(json.loads((folder / 'desktop-settings.json').read_text())['other'], 'kept')
        save_launch_chime(folder, True)
        self.assertTrue(launch_chime_enabled(folder))


if __name__ == '__main__':
    unittest.main()
