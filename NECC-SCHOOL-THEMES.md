# NECC school directory and desktop themes

The desktop app ships with 123 Rainbow Six team registrations across 96 schools,
captured from the [NECC LeagueOS season](https://necc.v1.leagueos.gg/league/seasons/2ek7kqsupq1csjr1f4ew1ya0i/rosters)
on September 26, 2026. Names, placement groups, team identifiers, primary colors
and logo URLs come from the public roster cards. This is a dated snapshot, not a
live standings feed. The source page remains linked from League & Team.

Since 1.6.0, NECC is one of the places a team can say it competes, next to every
Liquipedia event from S- to D-Tier and leagues of a team's own (see
[scripts/README.md](scripts/README.md#leagues-and-tournaments)). NECC's Rainbow
Six Divisions I–VI and their 128 teams are a separate snapshot,
`scripts/necc_divisions.json`, read from NECC's public season pages on
October 16, 2026.

Open **League & Team**, choose **NECC**, and search the school directory by school
or team. Apply a team's colors and logo, then open **Team Theme** to choose
primary/secondary colors, edit display names, upload a PNG/JPEG/WebP logo, or
restore the default theme. Uploaded
logos are decoded, resized and saved as PNG; the original file is not retained.
The selected identity styles every page, including match scores and stat cards.
Text accents are lightened for readability while decorative borders retain the
chosen school color.

Desktop preferences are saved atomically to `appearance.json` beside the season
database (normally `%LOCALAPPDATA%/R6MatchStats`). Uploaded logos are embedded in
that file and work offline. LeagueOS logos need an internet connection. Shared
web visitors receive session-only preferences, never the host's saved identity.

Player statistics are not invented or imported from LeagueOS display names.
Use exact Siege usernames in **Save team roster**, then import actual replays to
record their statistics. School/team-qualified names keep identically named
varsity squads separate. Existing JSON catalog imports and configured HTTPS
feeds remain available under **Update or import a directory**.

The API falls back to the same bundled directory for the React/Unity clients
when no custom feed or imported catalog is configured. The PyInstaller desktop
package includes the directory and theme pages. `test_branding.py` covers the
complete directory, preference persistence, invalid inputs and school selection.

School, league and game marks remain the property of their respective owners.
This independent community app does not imply endorsement by NECC or Ubisoft.
