# Free shared school workspace (draft)

This service uses Cloudflare Workers Free and D1, without Supabase, an identity provider,
or an email sender. It is a separate backend for the portable browser edition; the existing
Streamlit/desktop password-login databases do not synchronize with it yet.

Every address with the same exact school domain (captain@csp.edu, player@csp.edu) resolves
to one @csp.edu workspace. Subdomains are separate. Email format is checked only; affiliation
and ownership are not verified. The first creator gets a random 24-character hexadecimal
team PIN (96 bits) and a separate private recovery code. The creator shares the PIN privately.
Entering a school email alone cannot retrieve it. Roles describe responsibilities, not access.

The browser stores only an optional 12-hour bearer session, never the PIN, in localStorage.
Profiles, confirmed-player choices, rosters, seasons and match-day planning are shared through
explicit saves and refreshes. Each tab chooses its own viewing identity. Old revisions are
rejected, and failed saves remain exportable in memory. Personal and demo workspaces stay
separate. Workspace backups allowlist data fields and exclude authentication/session/recovery
fields; the private login backup is a separate deliberately labeled download.

## Deployment

Use Node 22+ with npm. Stay on Workers Free; no paid plan or custom domain is needed.

    npx wrangler login --scopes account:read user:read workers:write workers_scripts:write d1:write
    npx wrangler d1 create r6-team-workspace

Put the returned database ID into wrangler.jsonc, then:

    npx wrangler d1 migrations apply r6-team-workspace --remote
    # First, from the repository root: .venv\Scripts\python browser/build.py
    npx wrangler deploy

The returned HTTPS workers.dev URL also serves the browser app. Set R6_TEAM_SERVER_URL
to that URL and rebuild browser/build.py before a follow-up deploy to prefill the connection.
Use that same URL in a downloaded browser edition's Team hub. Everyone uses
that same server. Create a school workspace, privately save the PIN/recovery download, then
sign in. On another computer use another email at the same school plus that PIN. No real team
or credentials are preloaded. Do not upload local .wrangler state, account DBs or preview data.

## Local verification

    node test_worker.mjs
    npx wrangler d1 migrations apply r6-team-workspace --local
    npx wrangler dev --ip 127.0.0.1 --port 8787

The Node test uses SQLite with a D1-compatible adapter. Also tested the real local Wrangler
runtime from two browser tabs: a profile saved by one school email appeared for another
school email with the same PIN. Service tests cover isolation, stale revisions, expiry,
logout, rotating recovery, rate limits and exclusion of credentials from workspace exports.

## Pilot limits and remaining work

A shared workspace is capped at 1.5 MB of UTF-8 JSON to stay below D1's row limit. This is a
pilot limitation, not the 5 GB account storage quota. Large replay collections need chunked
or per-match storage before broad release. A valid PIN grants equal access to all data for
that exact school domain. A domain can be claimed by anyone with a correctly formatted
address: no affiliation is asserted. There is no email-based PIN retrieval or reset.

Desktop/Streamlit migration, automatic league-selection saves, remote end-to-end checks,
public deployment, scaling, and the original handoff's release checks remain outstanding.
Keep version 1.6.3 and PR 31 in draft; do not merge or release yet.

Official limits: https://developers.cloudflare.com/d1/platform/pricing/
Workers Free: https://developers.cloudflare.com/workers/platform/pricing/
