# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

## What's signed

The Windows app's own programs, from each release on the
[releases page](https://github.com/Animuxs/r6-stats-app/releases):

- `R6MatchStats-Setup.exe`, the installer, and the uninstaller it installs
- `R6MatchStats.exe`, the app
- `r6-dissect.exe`, the replay parser the app runs

Third-party files bundled with the app (Python and its libraries) keep their own
publishers' signatures, or none; they aren't signed with this certificate.

Signed files are built only from this repository's source, by the
[Windows app workflow](.github/workflows/windows-app.yaml) on GitHub-hosted runners when a
release is published. Nothing built on a personal computer is signed. Each release is scanned
with Microsoft Defender and installed and opened on a clean machine before it's published,
with SHA-256 checksums for every download.

## Team roles

- Committers and reviewers: [@atrejo26](https://github.com/atrejo26), [@julio208920](https://github.com/julio208920)
- Approvers: [@atrejo26](https://github.com/atrejo26)

Changes from anyone else are reviewed by a committer through a pull request before they're
merged. Every signing request is approved by an approver. Everyone in these roles uses
multi-factor authentication on GitHub and SignPath.

## Privacy

The Windows app runs on your PC. Your replays, stats and match history stay on your computer:
the app never uploads them, has no telemetry, and never connects to the game, Ubisoft's
servers or your Ubisoft account.

It connects to other sites only for these:

- **Team rosters**, at most once a day when the app opens: it downloads this project's
  published NECC roster list (`scripts/necc_rosters.json`) from GitHub
  (raw.githubusercontent.com). It sends nothing but the request itself, which names the app
  and its version.
- **Liquipedia events**, only when you click **Check Liquipedia for new events**: it reads
  the public event lists from liquipedia.net.
- **Team logos**, only when your team theme uses a logo from the web, such as a NECC school's
  logo hosted by LeagueOS (images.leagueos.gg) or a link you entered: the app's window loads
  that image from its site.
- **Get the Windows app**, only when you open that page: it looks up the newest release on
  GitHub (api.github.com).
- **Links you click**, such as a match video you added, open in your web browser.

Like any web request, each of these shows that site your IP address. GitHub, Liquipedia and
LeagueOS each have their own privacy policies.
