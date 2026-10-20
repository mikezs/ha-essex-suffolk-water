<img src="custom_components/essex_suffolk_water/brand/logo.png" alt="Essex & Suffolk Water" width="360">

# Essex & Suffolk Water for Home Assistant

[![hacs][hacs-badge]][hacs]
[![validate][validate-badge]][validate-workflow]

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.][my-badge]][my-link]

A Home Assistant custom integration that ingests **smart water meter** usage
from [Essex & Suffolk Water][esw] (part of Northumbrian Water Group) and feeds
it into the **Energy dashboard**'s water section as long-term statistics —
plus glanceable per-meter sensors.

It is built on the [`eswater`][eswater] async client, which reverse-engineers the
ESW online-account portal API.

> ⚠️ **Alpha.** The ESW/NWG API is private and undocumented; it may change or
> break at any time. Not affiliated with or endorsed by Essex & Suffolk Water.
> For personal use with your own account.

## Features

- 📊 **Long-term statistics** — hourly water usage (litres) and cost (GBP)
  backfilled into HA statistics, ready for the **Energy → Water** dashboard.
- 🚰 **Per-meter device** with sensors: latest daily consumption, latest daily
  cost, last reading time, and the meter register reading.
- 🔁 **Automatic backfill** of history on first setup (paced so it stays polite
  to the portal), then incremental hourly updates.
- 🔐 **Config flow** with a **re-authentication** prompt when your password
  changes or the session is rejected.

## Requirements

- Home Assistant 2025.11 or newer (the recorder statistics metadata API is required).
- An Essex & Suffolk Water online account (email + password) with a **smart
  meter**. Usage data lags roughly 1–2 days behind real time.

## Installation

### HACS (one-click)

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.][my-badge]][my-link]

Click the badge above, then **Download**, and restart Home Assistant. (Works as a
custom repository today; it also resolves directly once the integration is in the
default HACS store.)

### HACS (custom repository, manual)

1. In HACS, open the ⋮ menu → **Custom repositories**.
2. Add `https://github.com/mikezs/ha-essex-suffolk-water` with category
   **Integration**.
3. Search for **Essex & Suffolk Water**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/essex_suffolk_water` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

1. **Settings → Devices & Services → Add Integration**.
2. Search for **Essex & Suffolk Water**.
3. Enter the **email** and **password** for your online account.

The integration discovers your meters automatically and creates one device per
meter. History backfill runs in the background; large histories take a while on
first run because the portal serves history one day at a time.

## Energy dashboard

1. **Settings → Dashboards → Energy**.
2. Under **Water consumption**, add the statistic named
   `Essex & Suffolk Water … water usage` (statistic id
   `essex_suffolk_water:<account>_<serial>_usage`).
3. Optionally attach the matching `… water cost` statistic for cost tracking.

Statistics also appear under **Developer Tools → Statistics** (filter for
`essex_suffolk_water`).

## Troubleshooting

- **Re-authentication requested** — your password likely changed; enter the new
  one when prompted.
- **No history / gaps** — the portal only reliably serves recent history; the
  integration backfills as far back as the API returns data and tolerates empty
  days. The most recent day or two may be provisional and can lag.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && mypy custom_components && pytest -v
```

Use Python 3.13 for the HA 2026.2 baseline or Python 3.14 for current HA. To develop both projects
side-by-side, also run `pip install -e ../python-eswater --no-deps` in this
environment. Each config entry owns a separate cookie session; credential checks
use a short-lived session that is detached on success or failure. HA retains
ownership of its connector; do not call `close()` on HA-created sessions.

## Releasing

Open **Actions → Release → Run workflow** on **main**. Choose a `patch`, `minor`
or `major` bump. Optionally set **eswater_version** to a stable version already
published on PyPI; leave it blank to keep the current library pin. Leave
**dry_run** checked to validate the token, proposed changes, tests, hassfest and
HACS without changing GitHub or publishing. Uncheck it for a real release.

For coordinated releases, publish the Python library first, then run this
workflow with its new version. Both `manifest.json` and `requirements_test.txt`
are re-pinned; minimum-HA CI reads the manifest pin automatically.

The manual workflow validates the proposed files, commits the version bump,
waits for **CI and Validate** on that exact commit, then pushes its version tag.
The tag-triggered run of the same workflow rechecks the code and publishes the
GitHub release HACS serves. Manual version commits and matching `vX.Y.Z` tags
still work.

### Release token

Create a **fine-grained personal access token** owned by `mikezs`, restricted to
`ha-essex-suffolk-water` and `python-eswater`, with **Contents: read and write**
and the automatically included Metadata read permission. Add it as repository
Actions secret **`RELEASE_TOKEN`** in both repositories (Settings → Secrets and
variables → Actions). Renew the secret before token expiry; never put its value
in workflow inputs or source code. Workflows write permission is not required:
version/library-pin changes do not modify workflow files.

The owner's existing admin-role bypass in the `main-ci` ruleset permits the
version commit to be pushed before its remote checks exist. Local runner tests
pass before that push; remote CI/Validate must pass before tagging. A failed or
timed-out check leaves a version commit on main but no tag/release. Inspect/fix
it before preparing another release, or tag that exact commit manually once
its checks pass.

The manual checkout and git pushes use `RELEASE_TOKEN` so their events trigger
CI and the tag publisher. The built-in `GITHUB_TOKEN` is used for read-only CI
monitoring/HACS validation and, in the tag publisher only, creating the GitHub
release. The dry run verifies token authentication and `git push --dry-run`;
actual protected-branch writes also require the owner's ruleset bypass.

## License

MIT — see [LICENSE](LICENSE).

[esw]: https://www.eswater.co.uk
[eswater]: https://github.com/mikezs/python-eswater
[hacs]: https://hacs.xyz
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate-badge]: https://github.com/mikezs/ha-essex-suffolk-water/actions/workflows/validate.yml/badge.svg
[validate-workflow]: https://github.com/mikezs/ha-essex-suffolk-water/actions/workflows/validate.yml
[my-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[my-link]: https://my.home-assistant.io/redirect/hacs_repository/?owner=mikezs&repository=ha-essex-suffolk-water&category=integration
