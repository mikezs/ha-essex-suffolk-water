<img src="custom_components/essex_suffolk_water/brand/logo.png" alt="Essex & Suffolk Water" width="360">

# Essex & Suffolk Water for Home Assistant

[![hacs][hacs-badge]][hacs]
[![validate][validate-badge]][validate-workflow]

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

- Home Assistant 2025.2 or newer.
- An Essex & Suffolk Water online account (email + password) with a **smart
  meter**. Usage data lags roughly 1–2 days behind real time.

## Installation

### HACS (custom repository)

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

## License

MIT — see [LICENSE](LICENSE).

[esw]: https://www.eswater.co.uk
[eswater]: https://github.com/mikezs/python-eswater
[hacs]: https://hacs.xyz
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate-badge]: https://github.com/mikezs/ha-essex-suffolk-water/actions/workflows/validate.yml/badge.svg
[validate-workflow]: https://github.com/mikezs/ha-essex-suffolk-water/actions/workflows/validate.yml
