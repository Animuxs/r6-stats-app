"""Team identity controls: colors, names and logo, starting from your team (any NECC team: its own
colors and logo) or a NECC school if you like."""
import streamlit as st
from app_info import desktop_data_dir, is_windows_app
from desktop_settings import launch_chime_enabled, save_launch_chime
import rosters
from branding import DEFAULT_THEME, apply_theme, current_competition, current_theme, local_preferences, uploaded_logo
from necc_data import bundled_school_catalog
from team_ui import team_card, team_css
from tab import Tab


class ThemeTab(Tab):
    """Your team's colors, names and logo, from your team, a NECC school, or your own."""

    title, name = 'Team colors & identity', 'theme'

    def heading(self) -> None:
        st.caption('PERSONALIZE YOUR COMMAND CENTER')
        st.title(self.title)

    def render(self) -> None:
        st.write('Make every match report and analytics page feel like home.')
        if is_windows_app():
            with st.expander('Desktop options'):
                folder = desktop_data_dir()
                enabled = launch_chime_enabled(folder)
                chosen = st.toggle('Play a launch chime', value=enabled, key='desktop_launch_chime',
                                   help='Uses your Windows sound scheme once when startup succeeds.')
                if chosen != enabled:
                    try:
                        save_launch_chime(folder, chosen)
                    except OSError:
                        st.error('Could not save the chime setting. Check access to the app data folder.')
        theme = current_theme()
        schools = st.session_state.get('necc_schools') or bundled_school_catalog()
        team_css()
        left, right = st.columns([2, 1], gap='large')
        with left:
            teams = sorted(rosters.teams(), key=lambda t: (t['name'].casefold(), t['division']))
            if teams:
                st.subheader('Start with your team')
                mine = rosters.mine(current_competition())
                index = next((i for i, t in enumerate(teams) if mine and rosters.team_key(t) == rosters.team_key(mine)), 0)
                picked = st.selectbox('Team', teams, index=index, key='theme_team',
                                      format_func=lambda t: f"{t['name']} · {t['school']} · Division {t['division']}")
                st.markdown(team_card(picked, mine=bool(mine) and rosters.team_key(picked) == rosters.team_key(mine)),
                            unsafe_allow_html=True)
                if st.button("Use this team's colors and logo", type='primary', key='theme_team_apply'):
                    try:
                        apply_theme(rosters.theme_of(picked))
                        st.rerun()
                    except OSError as error:
                        st.error(f'Could not save appearance: {error}')
                st.caption('Its primary and secondary colors and its logo, as NECC shows them. Saving your team on '
                           '**League & Team** does this for you.')
                st.divider()
            st.subheader('Start with a NECC school')
            selected = st.selectbox('School preset', schools, format_func=lambda s: s['name'],
                index=next((i for i, s in enumerate(schools) if s['name'] == theme['name']), 0))
            team = st.selectbox('Team preset', selected['teams'], format_func=lambda t: t['name'])
            if st.button('Apply school colors and logo', type='primary'):
                try:
                    apply_theme(dict(name=selected['name'], team=team['name'],
                        primary=team.get('primary_color') or selected.get('primary_color') or DEFAULT_THEME['primary'],
                        secondary=DEFAULT_THEME['secondary'], logo=team.get('logo_url') or selected.get('logo_url') or ''))
                    st.rerun()
                except OSError as error:
                    st.error(f'Could not save appearance: {error}')
            st.divider()
            st.subheader('Customize your identity')
            with st.form('custom_appearance'):
                name = st.text_input('School or organization name', theme['name'], max_chars=180)
                team_name = st.text_input('Team display name', theme['team'], max_chars=180)
                a, b = st.columns(2)
                primary = a.color_picker('Primary color', theme['primary'])
                secondary = b.color_picker('Secondary color', theme['secondary'])
                logo_file = st.file_uploader('Your logo', type=['png', 'jpg', 'jpeg', 'webp'], help='Up to 2 MB. Saved with your theme on this computer.')
                remove_logo = st.checkbox('Use text identity without a logo')
                saved = st.form_submit_button('Save my theme', type='primary')
            if saved:
                try:
                    logo = '' if remove_logo else uploaded_logo(logo_file.getvalue()) if logo_file else theme['logo']
                    apply_theme(dict(name=name.strip() or 'My team', team=team_name.strip(), primary=primary, secondary=secondary, logo=logo))
                    st.rerun()
                except (ValueError, OSError) as error:
                    st.error(f'Could not save theme: {error}')
        with right:
            st.subheader('Current identity')
            if theme['logo']:
                st.image(theme['logo'], width=120)
            st.write(theme['name'])
            st.caption(theme['team'])
            st.caption('Your colors and logo style the banner, the sidebar, scoreboards, cards, charts, the docked panel '
                       'and the Windows app\'s launch screen. Text accents adapt for readability on dark backgrounds.')
            st.info('Saved on this computer across restarts.' if local_preferences() else 'Theme stays in this browser session. The desktop app saves it across restarts.')
            if st.button('Restore the default theme'):
                try:
                    apply_theme(DEFAULT_THEME)
                    st.rerun()
                except OSError as error:
                    st.error(f'Could not reset appearance: {error}')
            st.caption('Pick the league or tournament you play in on **League & Team**. Independent community tool: school, team and league marks belong to their owners.')


ThemeTab().run()
