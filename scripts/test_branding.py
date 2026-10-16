import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from streamlit.testing.v1 import AppTest
import branding
import necc_data


class TestSchoolIdentity(unittest.TestCase):
    def test_complete_verified_directory(self):
        schools = necc_data.bundled_school_catalog()
        teams = [team for school in schools for team in school['teams']]
        self.assertEqual(len(schools), 96)
        self.assertEqual(len(teams), 123)
        self.assertEqual(len({team['id'] for team in teams}), 123)
        self.assertTrue(all(team['logo_url'].startswith('https://images.leagueos.gg/') for team in teams))
        self.assertIn('Université de Sherbrooke', [s['name'] for s in schools])

    def test_theme_roundtrip_and_corrupt_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'appearance.json'
            theme = dict(branding.DEFAULT_THEME, name='Université', primary='#123456')
            branding.save_theme(theme, path)
            self.assertEqual(branding.load_theme(path), theme)
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(branding.load_theme(path), branding.DEFAULT_THEME)

    def test_rejects_unsafe_styles_and_logo_urls(self):
        theme = branding.clean_theme(dict(primary='red;}</style>', logo='javascript:alert(1)'))
        self.assertEqual(theme['primary'], branding.DEFAULT_THEME['primary'])
        self.assertEqual(theme['logo'], '')
        self.assertEqual(branding.clean_theme(dict(logo='data:image/png;base64,YmFk'))['logo'], '')

    def test_logo_is_decoded_and_normalized(self):
        file = io.BytesIO()
        Image.new('RGB', (800, 400), 'red').save(file, 'JPEG')
        logo = branding.uploaded_logo(file.getvalue())
        self.assertTrue(logo.startswith('data:image/png;base64,'))
        self.assertEqual(branding.clean_theme(dict(logo=logo))['logo'], logo)
        with self.assertRaises(ValueError):
            branding.uploaded_logo(b'x' * (2 * 1024 * 1024 + 1))

    def test_school_preset_updates_identity_and_search(self):
        with mock.patch.dict('os.environ', {'R6_HOSTED': '1'}):
            app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=60).run()
            app.switch_page('appearance.py').run()
            self.assertFalse(app.exception)
            next(b for b in app.button if b.label == 'Apply school colors and logo').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['appearance']['name'], 'Alpena Community College')
            app.switch_page('league.py').run()
            next(r for r in app.radio if r.label == 'Where do you compete?').set_value('NECC').run()
            next(t for t in app.text_input if t.label == 'Search school or team').set_value('Sherbrooke').run()
            self.assertFalse(app.exception)
            self.assertEqual(next(s for s in app.selectbox if s.label == 'School').options, ['Université de Sherbrooke'])


if __name__ == '__main__':
    unittest.main()
