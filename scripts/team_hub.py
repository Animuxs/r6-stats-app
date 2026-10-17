"""Shared team login and descriptive member profiles, with no external account provider."""
import secrets

import streamlit as st

from tab import Tab
from team_accounts import AccountError, ROLES
from team_runtime import current_team, storage_folder, store, switch_account


class TeamHubTab(Tab):
    title, name = 'Team hub', 'team'

    def render(self):
        st.caption('One team workspace • shared login • player identities and staff roles')
        st.info('School emails are checked for a .edu ending only. Email ownership and school affiliation are not verified. '
                'Everyone using the shared login has the same access; member roles describe responsibilities.')
        if self.session.demo:
            st.warning('Leave the demo season from the sidebar before signing in or changing team members.')
            return
        if storage_folder() is None:
            st.warning('Team login is unavailable on this host because it has no configured persistent team storage. '
                       'The free local app can host a team workspace. Hosted deployments need a persistent volume with R6_TEAM_DATA_DIR configured.')
            return
        if recovery := self.session.get('team_new_recovery'):
            st.warning('Save this recovery code privately. Anyone with it can reset the shared password. '
                       'There is no email recovery service; the code is shown until you dismiss it.')
            st.code(recovery)
            st.download_button('Download recovery code', recovery, file_name='R6-Team-Recovery.txt', mime='text/plain')
            if st.button('I saved the recovery code'):
                self.session.pop('team_new_recovery')
                self.session.drop('team_signup_')
                self.session.drop('team_reset_')
                st.rerun()
        account = current_team()
        if not account:
            st.caption('Accounts belong to the server hosting this page. To share across computers, everyone must '
                       'open the same team server address. Separately launched desktop apps do not automatically synchronize accounts.')
            self.sign_in()
            return
        st.subheader(account['name'])
        st.caption(f"Shared school address: {account['email']} · format checked, not verified")
        left, right = st.columns(2)
        if left.button('Refresh shared workspace', width='stretch'):
            token, person = self.session.team_token, self.session.team_person
            switch_account(token)
            self.session.team_person = person
            st.rerun()
        if right.button('Sign out of this device', width='stretch'):
            store().logout(self.session.team_token)
            switch_account()
            st.rerun()
        st.caption('Changes appear when teammates refresh or interact with their page. Each person signs in to the same '
                   'server and chooses their own viewing identity; personal and demo stats stay separate.')
        self.members(account)

    def sign_in(self):
        login, create, recover = st.tabs(['Sign in', 'Create team login', 'Recover login'])
        client = st.context.ip_address or 'local'
        with login, st.form('team_login'):
            email = st.text_input('School email', key='team_login_email', max_chars=254)
            password = st.text_input('Team password', type='password', key='team_login_password', max_chars=256)
            if st.form_submit_button('Sign in to team', type='primary'):
                token = store().login(email, password, client=client)
                switch_account(token)
                st.rerun()
        with create, st.form('team_signup'):
            name = st.text_input('Team name', max_chars=100, key='team_signup_name')
            email = st.text_input('School email ending in .edu', max_chars=254, key='team_signup_email')
            password = st.text_input('Choose a shared password', type='password', max_chars=256, key='team_signup_password')
            repeat = st.text_input('Repeat shared password', type='password', max_chars=256, key='team_signup_repeat')
            st.caption('Use at least 12 characters. Share credentials privately with your team. A recovery code will be generated.')
            if st.form_submit_button('Create team account'):
                if password != repeat:
                    raise AccountError('The passwords do not match.')
                self.session.set('team_new_recovery', store().create(email, password, name, client=client))
                st.rerun()
        with recover, st.form('team_reset'):
            email = st.text_input('Account school email', max_chars=254, key='team_reset_email')
            code = st.text_input('Saved recovery code', type='password', max_chars=256, key='team_reset_code')
            password = st.text_input('New shared password', type='password', max_chars=256, key='team_reset_password')
            repeat = st.text_input('Repeat new password', type='password', max_chars=256, key='team_reset_repeat')
            st.caption('Resetting signs out all devices and replaces the recovery code.')
            if st.form_submit_button('Reset team password'):
                if password != repeat:
                    raise AccountError('The passwords do not match.')
                self.session.set('team_new_recovery', store().reset_password(email, code, password, client=client))
                st.rerun()

    def members(self, account):
        people = account['directory']
        by_id = {p['id']: p for p in people}
        labels = lambda key: 'Team overview / no selected player' if not key else by_id[key]['name']
        choice = self.session.team_person
        options = [''] + list(by_id)
        chosen = st.selectbox('Who are you viewing as?', options,
                              index=options.index(choice) if choice in options else 0, format_func=labels)
        if chosen != choice:
            self.session.team_person = chosen
            st.rerun()
        if people:
            st.dataframe([{'Name': p['name'], 'Player username': p['player'] or '—',
                           'Roles': ', '.join(p['custom_role'] if r == 'Other' else r for r in p['roles'])}
                          for p in people], hide_index=True, width='stretch')
        st.subheader('Manage members and roles')
        selected = st.selectbox('Member to edit', [''] + list(by_id),
                                format_func=lambda key: 'Add a member' if not key else by_id[key]['name'])
        person = by_id.get(selected, {'name': '', 'player': '', 'roles': ['Player'], 'custom_role': ''})
        # Keep the displayed revision tied to this editor; concurrent saves must not silently overwrite.
        revision_key = f'team_directory_revision_{selected}'
        revision = self.session.setdefault(revision_key, account['revision'])
        with st.form('team_member_' + selected):
            name = st.text_input('Display name', value=person['name'], max_chars=100)
            player = st.text_input('Player username (optional for staff)', value=person['player'], max_chars=100)
            roles = st.multiselect('Team roles', ROLES, default=person['roles'])
            custom = st.text_input('Custom role when Other is selected', value=person['custom_role'], max_chars=100)
            if st.form_submit_button('Save member', type='primary'):
                updated = {'id': selected or secrets.token_hex(16), 'name': name, 'player': player,
                           'roles': roles, 'custom_role': custom}
                store().save_directory(self.session.team_token,
                                       [p for p in people if p['id'] != selected] + [updated], revision)
                self.session.drop('team_directory_revision_')
                st.rerun()
        if selected:
            confirmed = st.checkbox('Confirm removal of this member profile (match stats are kept)')
            if st.button('Remove member profile', disabled=not confirmed):
                store().save_directory(self.session.team_token, [p for p in people if p['id'] != selected], revision)
                self.session.drop('team_directory_revision_')
                st.rerun()


TeamHubTab().run()
