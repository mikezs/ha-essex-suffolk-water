# Essex & Suffolk Water

Ingest **smart water meter** usage from Essex & Suffolk Water (Northumbrian
Water Group) into Home Assistant.

- 📊 Hourly usage (litres) and cost (GBP) as long-term statistics for the
  **Energy → Water** dashboard.
- 🚰 A device per meter with latest daily consumption, daily cost, last reading
  time, and meter register sensors.
- 🔁 Automatic, paced history backfill on first setup.
- 🔐 Config flow with re-authentication support.

Sign in with the **email and password** of your Essex & Suffolk Water online
account. Requires a smart meter; usage data lags ~1–2 days.

> Alpha software using a private, undocumented API. Not affiliated with Essex &
> Suffolk Water. For personal use with your own account.
