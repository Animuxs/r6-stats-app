# Team login implementation checkpoint

Status: **draft, not released or connected to the app navigation yet**. The app version
remains 1.6.3. Do not advertise working team login until integration and end-to-end
testing are finished. No Supabase, external account provider or email sender is used.

## Confirmed product decisions

- One shared team login, created by a coach/analyst using an address ending in `.edu`.
- Check the email's format only. Do not label the address, school or team as verified.
- A shared login grants identical access to everyone using it. Person selection and
  Player, Captain, Coach, Analyst, Staff and Other/custom roles are descriptive profiles,
  not authentication or separate permission levels. A person can have multiple roles.
- Keep personal, team and demo workspaces separate; preserve all 1.6.3 features.
- Add a basic Windows launch chime and a mute setting.
- User asked to save progress before usage is exhausted; do not spend their reset.

## Implemented

`scripts/team_accounts.py` provides a SQLite-backed account foundation: school-format
validation, 600,000-round PBKDF2-SHA256 with random salts, hashed random session tokens,
12-hour session expiry, logout, failed-login/recovery throttling, one-time rotating
recovery codes, password recovery that revokes existing sessions, per-account folders,
validated team directories and optimistic revision checks for directory updates.
Neither passwords nor recovery codes nor bearer tokens are stored in plaintext.

`scripts/team_runtime.py` is a **not-yet-wired** Streamlit adapter. Account switching
clears session state and query parameters to prevent cross-account replay/theme/identity
leaks. It validates server-side sessions before resolving team folders. Local storage
defaults beside the stats database. Public hosting requires an explicitly configured
`R6_TEAM_DATA_DIR` on a persistent volume; disposable Community Cloud disk must not be
presented as durable account storage. Merely setting this variable does not provision
a persistent volume or enable a finished login UI.

The Windows launcher plays `SystemAsterisk` asynchronously after successful startup.
System sound schemes and volume settings apply. Muting is available under Team Theme
→ Desktop options → Play a launch chime. The preference persists in the app's local
`desktop-settings.json`. Audio errors cannot block launch; CI smoke runs remain silent.

## Remaining before release

1. Build the Team hub page: create/sign in/sign out/recover, one-time recovery-code
   presentation, shared team directory editing and per-session person selection.
2. Wire authenticated team database paths into **both** stats and season tracking.
   Apply player selection consistently to Dashboard, Ask, History and Operators, without
   changing the underlying match recorder. Avoid an import cycle between stats_db,
   sources and team_runtime (use narrow local imports or dependency injection).
3. Scope match-day planning, league and theme to the team, with concurrent-edit checks.
   Switching account, leaving a team and entering/leaving demo must not leak state.
4. Plan a portable backup/restore format, protecting credentials and replay privacy.
   Preserve imported player selections and independent season/built-team deletion.
5. Provide the same team/profile workflow in the standalone browser edition, with a
   clear boundary between offline data and the online shared workspace. No unsupported
   claim that an offline HTML file alone can host shared authentication.
6. Finish navigation/UI cleanup and responsive visual checks; retain all current tabs,
   detailed stats, native docking, themes, exports, roster controls and replay features.
7. Review signup abuse limits, recovery UX, account backup, concurrent writes, session
   revocation/expiry and deployment storage. Use HTTPS for credentials outside localhost;
   do not expose a local server publicly as part of setup without explicit configuration.
8. Test real two-session sharing/isolation, role/profile changes, stale updates,
   password recovery, demo isolation and desktop/hosted/browser behavior. Recheck other
   in-flight branches (including `fix/season-team-slider`) before integrating.
9. Only then bump the next version, create the release, wait for installer smoke checks
   and attach verified downloads. Do not merge or publish this checkpoint as complete.

## Validation commands

```
.venv\Scripts\python -m unittest discover -s scripts -p test_team_accounts.py
.venv\Scripts\python -m unittest discover -s scripts
.venv\Scripts\python -m unittest discover -s desktop
git diff --check
```

Parser-dependent application tests need `R6_DISSECT_BIN` pointing at a built parser.
The account tests cover credential hashing, session expiry/revocation, recovery rotation,
team isolation, stale directory writes, role validation, lockout and saved chime settings.
Desktop tests mock audio to check asynchronous playback, mute, smoke-run silence and
audio-device failure handling. A real packaged audio/UI check remains for the release.
