# Team login checkpoint - draft PR 31

Branch: feat/team-login. App version stays 1.6.3. No merge or new release yet.
No Supabase, external identity provider, email codes or email sender.

## User decisions

- One shared team login using an address ending in .edu. This is a format check,
  NOT ownership verification or proof of school affiliation.
- Each person selects a profile. Multiple descriptive roles: Player, Captain,
  Coach, Analyst, Staff, Other/custom. Everyone sharing the login has equal access.
- Preserve desktop, hosted and standalone features; keep team/personal/demo data apart.
- Windows launch chime with an off switch.
- Continue until around/below 10% usage remains, then save; do not use the free reset.
- Keep this handoff current across conversation compaction.

## Implemented and wired in desktop/Streamlit

- Team hub navigation, sign-in, account creation, password recovery and sign-out.
- Recovery code download/display with explicit private-backup guidance and dismissal.
- Member add/edit/remove, multiple roles, custom titles, per-session viewing identity.
- Account switching clears cached replay/theme/planning/player state and query params.
- Shared account DB paths for stats and seasons; player perspective for History,
  Ask and Operators without changing the replay recorder. Existing Dashboard scoreboard
  and replay identity remain accurate to the original replay.
- Shared planning/theme/competition documents and optimistic revisions; explicit Refresh
  shared workspace discards stale form state. Stale writes produce a recoverable message.
- Team overview caption, no companion rail on Team hub, personal/demo isolation.
- PBKDF2-SHA256 (600,000 rounds/random salts), hashed random session tokens, 12-hour expiry,
  revoked sessions on logout/recovery, rotating recovery codes. Signup/login/recovery limits.
- Per-account server folders resolved only through authenticated opaque sessions.
- Local storage defaults beside the stats DB. Hosted deployments require an explicit
  R6_TEAM_DATA_DIR pointing at a genuinely persistent volume. Merely setting the variable
  does not create persistence. Existing Community Cloud demo has NO persistent team
  storage, so login is intentionally unavailable there until durable hosting is chosen.
- Windows SystemAsterisk chime after successful startup; asynchronous, muted for smoke
  tests, errors ignored. Team Theme > Desktop options > Play a launch chime persists the
  setting in desktop-settings.json. Windows sound scheme/volume settings apply.

## Checks and preview

- Latest full application and desktop test results are in work/team-login-final-tests.log.
- Account lifecycle tests plus eight integrated Team hub tests cover sign-in/out, two-session
  stats sharing, team isolation, profile roles, selected-player Operators, revoked sessions,
  concurrent-document rejection, demo isolation, signup and password-recovery UI. Desktop tests cover audio behavior.
- Preview server: http://127.0.0.1:8504/team_hub (local machine only), logs in
  work/team-login-preview.log and work/team-login-preview-error.log.
- Preview data is OUTSIDE the repo in work/team-login-preview-data, includes only a
  clearly labeled sample team. Do not publish it, its account DB or its test credentials.
- Team hub screenshot saved in outputs/team-hub-preview.jpg.
- No authenticated bearer tokens or real credentials are committed to Git.
- Latest checks: full 299-test application suite and 29 desktop tests passed; the
  expanded eight-test Team hub suite also passed (includes two added signup/recovery cases).

## Required work before release

1. Standalone browser parity: profile management, identities, shared server connection,
   portable import/export. An offline HTML file cannot host shared authentication alone.
   Use an explicit online workspace workflow; do not imply local browser storage is shared.
2. A durable public team host/storage decision. Local team accounts on separate PCs are
   separate databases. Everyone must use the same server URL to share. Do not expose an
   HTTP local server to the internet as an automatic setup step. HTTPS is required outside
   localhost/trusted deployment. The current public demo has no account persistence.
3. Safe shared team backup/restore (exclude passwords/session/recovery credentials),
   migration/import of existing personal/browser stats with selected-player preservation.
4. More concurrent planning/theme edits,
   selected-profile deletion/change across sessions, account expiry mid-operation, responsive
   visuals, packaged chime playback. Check duplicate/stale widget behavior carefully.
5. Review the role labels and team-overview perspective: no chosen linked player falls
   back to the recorder for per-player analytics; roles are never authorization boundaries.
6. Recheck other branches/PRs (especially fix/season-team-slider) before integration. Preserve
   OOP tabs, full stats in both team views, roster controls, independent team deletion,
   selected imports, Ask, history, seasons, exports, themes, docking and replay controls.
7. Run final app/desktop/browser checks, bump the next version only when complete, merge,
   publish, wait for Windows build/Defender/install/launch/uninstall and verify download hashes.

## Verification commands

    .venv\Scripts\python -m unittest discover -s scripts
    .venv\Scripts\python -m unittest discover -s desktop
    git diff --check

Set R6_DISSECT_BIN to a built parser for parser-dependent tests. No new Python runtime
package is required. New modules are included in the Windows packaging manifest.
